/*
 * Hexcast Games — Blackjack renderer                          static/blackjack.js
 *
 * Shared by the OBS overlay (/games/overlay) and the control panel (/games#blackjack).
 * Plain browser script: no modules, no dependencies, no build step. Everything is drawn on one
 * canvas (vector art: the felt, the cards, the chips, Hex the dealer) - no images, no web fonts.
 *
 * Registers window.HexGames.blackjack:
 *
 *   BASE_W, BASE_H            scene size in stage px at scale 1 (1920 x 1080: the whole stage)
 *   THEMES                    { classic, neon, midnight, royal }
 *   DEFAULTS, APPEARANCE      same values / keys as the backend
 *   create(container, config, opts) -> instance      opts: {sound: true}
 *   sample(cfg, id, ms) / preview(cfg, id)           a sample STATE / a demo hand (the panel's editor)
 *
 *   instance.setConfig(cfg)   instance.resize()    instance.setState(STATE)
 *   instance.reset()          instance.destroy()
 *
 * setState() takes the game's whole STATE (the server's state_view(): {state, visible, game, idle,
 * preview, ...}); game.ends_in_ms / game.elapsed_ms must already be corrected for the time the
 * message sat in the host. The overlay only ANIMATES what the server decided: every card on the
 * felt is a function of the STATE and the time since its phase started, so a late join (or the
 * panel's mirror) lands on the same frame as every other overlay.
 *
 *   dealing    game.deal = {t0, step, order: [[seat, hand, card] ...]}: card k leaves the shoe at
 *              t0 + k * step (seat 0 = the dealer; the dealer's second card stays face down)
 *   resolve    the cards the action round's hits / doubles / splits need, the same way (+ moves:
 *              [[seat, from_hand, to_hand]] - the second card of a split pair slides to its new hand)
 *   dealer     deal.flip = the hole card is turned at t0, then the dealer's draws come out
 *   settle     every hand carries its result {outcome, pay, net}: chips go to the winners / the house
 * The dealer's hole card is never in the STATE until it is turned over.
 */
(function (root) {
  'use strict';

  var HG = root.HexGames || (root.HexGames = {});
  var BASE_W = 1920, BASE_H = 1080;
  var TAU = Math.PI * 2;

  var DEFAULTS = {
    x: 50, y: 50, scale: 1.0, theme: 'classic', title: 'Blackjack', show_rules: true, show_players: true,
    players_max: 8, show_shoe: true, show_captions: true, sfx: true, sfx_volume: 0.6, hide_when_idle: true,
    commands_text: '', dealer_name: 'Hex', currency: 'hexcoins', min_bet: 1, max_bet: 100000, seats: 10,
    seat_fill: 'center', queue_max: 20, deck_table: '1:2,3:4,5:6,8:8', penetration_pct: 75,
    dealer_hits_soft_17: false, dealer_peeks: true, blackjack_pays: '3:2', double_on: 'any',
    double_after_split: true, max_hands: 4, resplit_aces: false, split_aces_one_card: true, insurance: true,
    surrender: false, five_card_charlie: false, open_bet_seconds: 30, bet_seconds: 15, insurance_seconds: 8,
    action_seconds: 12, later_action_seconds: 8, max_rounds: 8, settle_seconds: 7, summary_seconds: 8,
    card_ms: 240, idle_windows: 3, deal_clip: '', blackjack_clip: '', win_clip: ''
  };
  var APPEARANCE = ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'show_shoe',
    'show_captions', 'sfx', 'sfx_volume'];

  var SERIF = "Georgia,'Palatino Linotype','Book Antiqua','Liberation Serif','DejaVu Serif','Times New Roman',serif";
  var SANS = "'Segoe UI','Helvetica Neue',Arial,'Liberation Sans','DejaVu Sans',sans-serif";

  // ------------------------------------------------------------------ themes
  var THEMES = {
    classic: {
      feltA: '#168a60', feltB: '#0c5a3f', feltC: '#073725', hex: 'rgba(255,255,255,.060)', hexHi: 'rgba(255,255,255,.13)',
      rail1: '#5a371f', rail2: '#2c180b', railHi: 'rgba(255,220,170,.30)', trim: '#e6c06c', trimDim: 'rgba(230,192,108,.50)',
      ink: '#f8f0da', inkDim: 'rgba(248,240,218,.72)', accent: '#f0c75e', accent2: '#8f6bff',
      plate1: '#2f4a40', plate2: '#14261f', plateInk: '#fff7df', plateEdge: '#e6c06c',
      backA: '#33237c', backB: '#150d3e', backHex: 'rgba(255,255,255,.30)', glow: 0, visor: '#2f9d66'
    },
    neon: {
      feltA: '#141a2c', feltB: '#0a0e1a', feltC: '#04060d', hex: 'rgba(0,240,255,.075)', hexHi: 'rgba(255,60,190,.22)',
      rail1: '#20243a', rail2: '#0a0c16', railHi: 'rgba(120,200,255,.35)', trim: '#00f0ff', trimDim: 'rgba(0,240,255,.50)',
      ink: '#f4fbff', inkDim: 'rgba(214,236,255,.72)', accent: '#ff3fb4', accent2: '#00f0ff',
      plate1: '#1b2038', plate2: '#0a0d1c', plateInk: '#eaffff', plateEdge: '#ff3fb4',
      backA: '#3a0f4f', backB: '#10061c', backHex: 'rgba(0,240,255,.42)', glow: 1, visor: '#12b5c8'
    },
    midnight: {
      feltA: '#1c4a8c', feltB: '#102f5f', feltC: '#081b3a', hex: 'rgba(255,255,255,.060)', hexHi: 'rgba(160,200,255,.20)',
      rail1: '#2d2f3a', rail2: '#12131a', railHi: 'rgba(210,220,255,.30)', trim: '#c9d6ee', trimDim: 'rgba(201,214,238,.50)',
      ink: '#f1f5ff', inkDim: 'rgba(222,232,255,.72)', accent: '#9ec2ff', accent2: '#ffd36b',
      plate1: '#26385a', plate2: '#0f1a30', plateInk: '#f4f8ff', plateEdge: '#c9d6ee',
      backA: '#2a3d86', backB: '#101a46', backHex: 'rgba(255,255,255,.30)', glow: 0, visor: '#3f7fd6'
    },
    royal: {
      feltA: '#7a1a43', feltB: '#4c0f2a', feltC: '#2a0617', hex: 'rgba(255,220,160,.060)', hexHi: 'rgba(255,220,160,.20)',
      rail1: '#3d2a12', rail2: '#1c1206', railHi: 'rgba(255,225,150,.34)', trim: '#f1cf6e', trimDim: 'rgba(241,207,110,.52)',
      ink: '#fff3d6', inkDim: 'rgba(255,243,214,.74)', accent: '#f6d674', accent2: '#c58bff',
      plate1: '#5a2540', plate2: '#2a0f1d', plateInk: '#fff3d6', plateEdge: '#f1cf6e',
      backA: '#6a1d4a', backB: '#2a0a1f', backHex: 'rgba(255,226,150,.38)', glow: 0, visor: '#c8963a'
    }
  };
  var C_WIN = '#59e08c', C_LOSE = '#ff6a60', C_PUSH = '#d4dbea', C_BJ = '#ffd54a', C_BUST = '#ff5047';

  // chip denominations: face, stripe, text
  var DENOMS = [
    { v: 1000000, t: '1M', main: '#c4cbd6', stripe: '#3f5f9a', ink: '#1c2a46' },
    { v: 100000, t: '100K', main: '#d6478e', stripe: '#fff0f6', ink: '#fff' },
    { v: 25000, t: '25K', main: '#1fa7b3', stripe: '#e6fdff', ink: '#fff' },
    { v: 5000, t: '5K', main: '#e5762c', stripe: '#fff1e4', ink: '#fff' },
    { v: 1000, t: '1K', main: '#e8bc22', stripe: '#3d2a00', ink: '#2c1d00' },
    { v: 500, t: '500', main: '#7c3fb5', stripe: '#f1e6ff', ink: '#fff' },
    { v: 100, t: '100', main: '#26262e', stripe: '#f4f4f4', ink: '#f2cf6a' },
    { v: 25, t: '25', main: '#2f9e5f', stripe: '#eafff2', ink: '#fff' },
    { v: 5, t: '5', main: '#d63a3a', stripe: '#fff0f0', ink: '#fff' },
    { v: 1, t: '1', main: '#eceff4', stripe: '#3a6ec9', ink: '#25408a' }
  ];

  // ------------------------------------------------------------------ helpers
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function merge(a, b) { var o = {}, k; for (k in a) if (has(a, k)) o[k] = a[k]; if (b) for (k in b) if (has(b, k) && b[k] != null) o[k] = b[k]; return o; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ease(t) { t = clamp(t, 0, 1); return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
  function easeOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }
  function easeIn(t) { t = clamp(t, 0, 1); return t * t; }
  function easeBack(t) { t = clamp(t, 0, 1); var c1 = 1.9, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); }
  function fmt(n) { n = Math.round(+n || 0); return String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',').replace(/^/, n < 0 ? '−' : ''); }
  function fmtShort(n) {
    n = Math.round(+n || 0);
    if (n >= 10000000) return Math.round(n / 1000000) + 'M';
    if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
    if (n >= 100000) return Math.round(n / 1000) + 'K';
    return fmt(n);
  }
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
  function mk(w, h) { var cv = root.document.createElement('canvas'); cv.width = Math.max(1, Math.round(w)); cv.height = Math.max(1, Math.round(h)); return cv; }

  function rrect(c, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    c.beginPath();
    c.moveTo(x + r, y); c.lineTo(x + w - r, y); c.arcTo(x + w, y, x + w, y + r, r);
    c.lineTo(x + w, y + h - r); c.arcTo(x + w, y + h, x + w - r, y + h, r);
    c.lineTo(x + r, y + h); c.arcTo(x, y + h, x, y + h - r, r);
    c.lineTo(x, y + r); c.arcTo(x, y, x + r, y, r); c.closePath();
  }
  function hexPath(c, cx, cy, r, rot) {
    c.beginPath();
    for (var i = 0; i < 6; i++) {
      var a = rot + i * TAU / 6, x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      if (i) c.lineTo(x, y); else c.moveTo(x, y);
    }
    c.closePath();
  }
  // a colour with its alpha replaced
  function rgba(hex, a) {
    var m = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!m) return hex;
    var n = parseInt(m[1], 16);
    return 'rgba(' + (n >> 16) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')';
  }
  function shade(hex, f) {      // f > 0 lighter, f < 0 darker
    var m = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!m) return hex;
    var n = parseInt(m[1], 16), r = n >> 16, g = n >> 8 & 255, b = n & 255;
    function s(v) { return Math.round(f >= 0 ? v + (255 - v) * f : v * (1 + f)); }
    return 'rgb(' + s(r) + ',' + s(g) + ',' + s(b) + ')';
  }
  function fit(c, text, font, maxW, size, minSize) {      // shrink the font until the text fits maxW; ellipsize at the minimum
    var s = size;
    c.font = font.replace('%s', s);
    while (c.measureText(text).width > maxW && s > minSize) { s -= 1; c.font = font.replace('%s', s); }
    if (c.measureText(text).width > maxW) {
      var t = text;
      while (t.length > 1 && c.measureText(t + '…').width > maxW) t = t.slice(0, -1);
      return t + '…';
    }
    return text;
  }

  // ------------------------------------------------------------------ cards
  var RANK_LABEL = { A: 'A', T: '10', J: 'J', Q: 'Q', K: 'K' };
  function rankLabel(r) { return RANK_LABEL[r] || r; }
  function cardPoints(code) { var r = code.charAt(0); return r === 'A' ? 1 : (r === 'T' || r === 'J' || r === 'Q' || r === 'K') ? 10 : +r; }
  function handValue(codes) {
    var t = 0, aces = 0;
    for (var i = 0; i < codes.length; i++) { if (!codes[i]) continue; t += cardPoints(codes[i]); if (codes[i].charAt(0) === 'A') aces++; }
    if (aces && t <= 11) return { total: t + 10, soft: true };
    return { total: t, soft: false };
  }
  function isRed(code) { var s = code.charAt(1); return s === 'H' || s === 'D'; }

  // a suit, centred on (0,0), about s px tall (the path is built, not filled)
  function suitPath(c, suit, s) {
    var h = s / 2;
    c.beginPath();
    if (suit === 'H') {
      c.moveTo(0, h * 0.98);
      c.bezierCurveTo(-h * 0.35, h * 0.72, -h * 1.12, h * 0.22, -h * 1.08, -h * 0.38);
      c.bezierCurveTo(-h * 1.04, -h * 0.9, -h * 0.35, -h * 1.0, 0, -h * 0.42);
      c.bezierCurveTo(h * 0.35, -h * 1.0, h * 1.04, -h * 0.9, h * 1.08, -h * 0.38);
      c.bezierCurveTo(h * 1.12, h * 0.22, h * 0.35, h * 0.72, 0, h * 0.98);
    } else if (suit === 'D') {
      c.moveTo(0, -h);
      c.bezierCurveTo(h * 0.16, -h * 0.5, h * 0.5, -h * 0.16, h * 0.74, 0);
      c.bezierCurveTo(h * 0.5, h * 0.16, h * 0.16, h * 0.5, 0, h);
      c.bezierCurveTo(-h * 0.16, h * 0.5, -h * 0.5, h * 0.16, -h * 0.74, 0);
      c.bezierCurveTo(-h * 0.5, -h * 0.16, -h * 0.16, -h * 0.5, 0, -h);
    } else if (suit === 'S') {
      c.moveTo(0, -h);
      c.bezierCurveTo(h * 0.28, -h * 0.62, h * 1.12, -h * 0.28, h * 1.04, h * 0.28);
      c.bezierCurveTo(h * 0.98, h * 0.74, h * 0.4, h * 0.82, h * 0.1, h * 0.46);
      c.bezierCurveTo(h * 0.14, h * 0.72, h * 0.2, h * 0.9, h * 0.46, h);
      c.lineTo(-h * 0.46, h);
      c.bezierCurveTo(-h * 0.2, h * 0.9, -h * 0.14, h * 0.72, -h * 0.1, h * 0.46);
      c.bezierCurveTo(-h * 0.4, h * 0.82, -h * 0.98, h * 0.74, -h * 1.04, h * 0.28);
      c.bezierCurveTo(-h * 1.12, -h * 0.28, -h * 0.28, -h * 0.62, 0, -h);
    } else {
      var r = h * 0.43;
      c.moveTo(0, -h * 0.02);
      c.arc(0, -h * 0.55, r, Math.PI * 0.5, Math.PI * 2.5, false);
      c.moveTo(-h * 0.5 + r, h * 0.2);
      c.arc(-h * 0.5, h * 0.2, r, 0, TAU, false);
      c.moveTo(h * 0.5 + r, h * 0.2);
      c.arc(h * 0.5, h * 0.2, r, 0, TAU, false);
      c.moveTo(-h * 0.1, h * 0.1);
      c.bezierCurveTo(-h * 0.08, h * 0.62, -h * 0.2, h * 0.88, -h * 0.5, h);
      c.lineTo(h * 0.5, h);
      c.bezierCurveTo(h * 0.2, h * 0.88, h * 0.08, h * 0.62, h * 0.1, h * 0.1);
      c.closePath();
    }
  }
  function suitFill(c, suit, s, color, x, y, flip) {
    c.save(); c.translate(x, y); if (flip) c.rotate(Math.PI);
    suitPath(c, suit, s); c.fillStyle = color; c.fill();
    c.restore();
  }

  // pip layouts: [col (-1,0,1), row (0 top .. 1 bottom)]
  var PIPS = {
    2: [[0, 0], [0, 1]], 3: [[0, 0], [0, 0.5], [0, 1]],
    4: [[-1, 0], [1, 0], [-1, 1], [1, 1]], 5: [[-1, 0], [1, 0], [0, 0.5], [-1, 1], [1, 1]],
    6: [[-1, 0], [1, 0], [-1, 0.5], [1, 0.5], [-1, 1], [1, 1]],
    7: [[-1, 0], [1, 0], [0, 0.25], [-1, 0.5], [1, 0.5], [-1, 1], [1, 1]],
    8: [[-1, 0], [1, 0], [0, 0.25], [-1, 0.5], [1, 0.5], [0, 0.75], [-1, 1], [1, 1]],
    9: [[-1, 0], [1, 0], [-1, 1 / 3], [1, 1 / 3], [0, 0.5], [-1, 2 / 3], [1, 2 / 3], [-1, 1], [1, 1]],
    10: [[-1, 0], [1, 0], [0, 1 / 6], [-1, 1 / 3], [1, 1 / 3], [-1, 2 / 3], [1, 2 / 3], [0, 5 / 6], [-1, 1], [1, 1]]
  };

  function crownPath(c, w, h) {      // a little crown centred on (0,0)
    c.beginPath();
    c.moveTo(-w / 2, h / 2); c.lineTo(-w / 2, -h * 0.1); c.lineTo(-w * 0.25, h * 0.15); c.lineTo(0, -h / 2);
    c.lineTo(w * 0.25, h * 0.15); c.lineTo(w / 2, -h * 0.1); c.lineTo(w / 2, h / 2); c.closePath();
  }

  // One card face / back, drawn at (0,0) with size w x h (device scale already in the transform).
  function drawFace(c, code, w, h) {
    var rank = code.charAt(0), suit = code.charAt(1), red = isRed(code);
    var col = red ? '#c8102e' : '#15151b', rad = Math.max(3, w * 0.075);
    // the card
    rrect(c, 0, 0, w, h, rad);
    var g = c.createLinearGradient(0, 0, w * 0.4, h);
    g.addColorStop(0, '#ffffff'); g.addColorStop(1, '#efece4');
    c.fillStyle = g; c.fill();
    c.lineWidth = Math.max(1, w * 0.014); c.strokeStyle = 'rgba(40,36,30,.55)'; c.stroke();
    // corner indices (jumbo: easy to read on a stream)
    var lab = rankLabel(rank), big = lab.length > 1;
    var fs = h * (big ? 0.215 : 0.265), ix = w * (big ? 0.05 : 0.075);
    c.font = '800 ' + fs + 'px ' + SANS;
    c.textAlign = 'left'; c.textBaseline = 'alphabetic'; c.fillStyle = col;
    var base = h * 0.03 + fs * 0.86, sw = c.measureText(lab).width;
    function corner(flip) {
      c.save();
      if (flip) { c.translate(w, h); c.rotate(Math.PI); }
      c.fillText(lab, ix, base);
      suitFill(c, suit, h * 0.135, col, ix + sw / 2, base + h * 0.095, false);
      c.restore();
    }
    corner(false); corner(true);
    // the middle
    var cx = w / 2, y0 = h * 0.2, y1 = h * 0.8;
    if (rank === 'A') {
      suitFill(c, suit, h * 0.4, col, cx, h * 0.5, false);
    } else if (PIPS[rank === 'T' ? 10 : +rank]) {
      var n = rank === 'T' ? 10 : +rank, ps = h * (n >= 9 ? 0.135 : n >= 6 ? 0.145 : n >= 4 ? 0.16 : 0.18);
      var lst = PIPS[n];
      for (var i = 0; i < lst.length; i++) {
        var px = cx + lst[i][0] * w * 0.15;
        var py = y0 + lst[i][1] * (y1 - y0);
        suitFill(c, suit, ps, col, px, py, lst[i][1] > 0.5);
      }
    } else {
      // J Q K: a framed court panel with the letter, a crown / plume, and the suit
      var pw = w * 0.44, ph = h * 0.72, px0 = (w - pw) / 2, py0 = h * 0.14;
      rrect(c, px0, py0, pw, ph, w * 0.04);
      var pg = c.createLinearGradient(px0, py0, px0 + pw, py0 + ph);
      pg.addColorStop(0, red ? '#fbe6e9' : '#e9e9ee'); pg.addColorStop(1, red ? '#f3c9cf' : '#d3d3dc');
      c.fillStyle = pg; c.fill();
      c.lineWidth = Math.max(1, w * 0.018); c.strokeStyle = col; c.stroke();
      c.save(); rrect(c, px0, py0, pw, ph, w * 0.04); c.clip();
      c.globalAlpha = 0.10; c.fillStyle = col; c.beginPath(); c.moveTo(px0, py0 + ph); c.lineTo(px0 + pw, py0); c.lineTo(px0 + pw, py0 + ph); c.closePath(); c.fill();
      c.restore();
      c.save(); c.translate(cx, py0 + ph * 0.2);
      if (rank === 'K' || rank === 'Q') {
        crownPath(c, pw * 0.56, ph * 0.12); c.fillStyle = '#e0a912'; c.fill(); c.lineWidth = Math.max(1, w * 0.012); c.strokeStyle = '#8a5d00'; c.stroke();
        c.fillStyle = col; c.beginPath(); c.arc(0, ph * 0.0, pw * 0.05, 0, TAU); c.fill();
      } else {
        c.rotate(-0.35); c.beginPath(); c.moveTo(0, ph * 0.07);
        c.bezierCurveTo(pw * 0.1, -ph * 0.1, pw * 0.38, -ph * 0.12, pw * 0.4, -ph * 0.02);
        c.bezierCurveTo(pw * 0.24, -ph * 0.02, pw * 0.1, ph * 0.04, 0, ph * 0.07); c.closePath();
        c.fillStyle = '#e0a912'; c.fill(); c.lineWidth = Math.max(1, w * 0.012); c.strokeStyle = '#8a5d00'; c.stroke();
      }
      c.restore();
      c.font = '700 ' + (h * 0.36) + 'px ' + SERIF; c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillStyle = col; c.fillText(rank, cx, py0 + ph * 0.55);
      suitFill(c, suit, h * 0.13, col, cx, py0 + ph * 0.87, false);
    }
    // a thin inner edge
    rrect(c, w * 0.018, w * 0.018, w * 0.964, h - w * 0.036, rad * 0.8);
    c.lineWidth = Math.max(0.6, w * 0.008); c.strokeStyle = 'rgba(0,0,0,.08)'; c.stroke();
  }

  // the card back: a honeycomb with the Hex mark
  function drawBack(c, w, h, th) {
    var rad = Math.max(3, w * 0.075);
    rrect(c, 0, 0, w, h, rad);
    c.fillStyle = '#fbfaf6'; c.fill();
    c.lineWidth = Math.max(1, w * 0.014); c.strokeStyle = 'rgba(40,36,30,.55)'; c.stroke();
    var m = w * 0.07, bw = w - 2 * m, bh = h - 2 * m;
    rrect(c, m, m, bw, bh, rad * 0.7);
    var g = c.createLinearGradient(0, 0, w, h);
    g.addColorStop(0, th.backA); g.addColorStop(1, th.backB);
    c.fillStyle = g; c.fill();
    c.save(); rrect(c, m, m, bw, bh, rad * 0.7); c.clip();
    var r = w * 0.135, hh = r * Math.sqrt(3);
    c.strokeStyle = th.backHex; c.lineWidth = Math.max(0.7, w * 0.012);
    for (var row = -1; row * hh * 0.5 < h + hh; row++) {
      for (var col = -1; col * r * 1.5 < w + r * 2; col++) {
        var cx = col * r * 1.5, cy = row * hh * 0.5 * 2 + (col % 2 ? hh / 2 : 0) * 1;
        hexPath(c, cx + m, cy + m, r * 0.94, 0); c.stroke();
      }
    }
    c.restore();
    // emblem: a hexagon with a cube in it (the Hex mark)
    var ex = w / 2, ey = h / 2, er = w * 0.27;
    hexPath(c, ex, ey, er, Math.PI / 6); c.fillStyle = 'rgba(8,6,28,.88)'; c.fill();
    c.lineWidth = Math.max(1, w * 0.025); c.strokeStyle = th.accent || '#f0c75e'; c.stroke();
    c.lineWidth = Math.max(0.8, w * 0.018); c.strokeStyle = th.accent || '#f0c75e';
    c.beginPath();
    c.moveTo(ex, ey); c.lineTo(ex, ey - er * 0.86);
    c.moveTo(ex, ey); c.lineTo(ex - er * 0.745, ey + er * 0.43);
    c.moveTo(ex, ey); c.lineTo(ex + er * 0.745, ey + er * 0.43);
    c.stroke();
  }

  // ------------------------------------------------------------------ chips
  function chipsFor(amount) {
    var out = [], left = Math.max(0, Math.round(amount));
    for (var i = 0; i < DENOMS.length; i++) {
      var n = Math.floor(left / DENOMS[i].v);
      if (n > 0) { out.push({ d: DENOMS[i], n: n }); left -= n * DENOMS[i].v; }
    }
    return out;
  }

  // One chip from above-and-in-front (an ellipse) at (cx, cy) = the centre of its TOP face, r = radius,
  // t = thickness. Side first, then the top. `ink` = draw the denomination on the top.
  function drawChipTop(c, cx, cy, r, d, t, ink) {
    var ry = r * 0.52;
    // side
    c.save();
    c.beginPath();
    c.moveTo(cx - r, cy); c.lineTo(cx - r, cy + t);
    c.ellipse(cx, cy + t, r, ry, 0, Math.PI, 0, true);
    c.lineTo(cx + r, cy); c.ellipse(cx, cy, r, ry, 0, 0, Math.PI, false); c.closePath();
    var sg = c.createLinearGradient(cx - r, 0, cx + r, 0);
    sg.addColorStop(0, shade(d.main, -0.35)); sg.addColorStop(0.35, d.main); sg.addColorStop(0.7, shade(d.main, -0.15)); sg.addColorStop(1, shade(d.main, -0.45));
    c.fillStyle = sg; c.fill();
    c.clip();
    // edge spots on the side (the stripes of a casino chip)
    c.fillStyle = d.stripe;
    for (var i = 0; i < 6; i++) {
      var a = Math.PI * (0.08 + i * 0.168), sx = cx + Math.cos(a + Math.PI) * r * 0.97, w2 = r * 0.2 * Math.abs(Math.sin(a));
      if (Math.sin(a) < 0.1) continue;
      c.fillRect(sx - w2 / 2, cy + ry * Math.sin(a) * 0.15 - 1 + (cy ? 0 : 0), w2, t + ry * 1.0);
    }
    c.restore();
    c.lineWidth = Math.max(0.6, r * 0.04); c.strokeStyle = 'rgba(0,0,0,.45)';
    c.beginPath(); c.ellipse(cx, cy + t, r, ry, 0, 0, Math.PI, false); c.stroke();
    // top face
    c.beginPath(); c.ellipse(cx, cy, r, ry, 0, 0, TAU);
    var tg = c.createRadialGradient(cx - r * 0.3, cy - ry * 0.4, r * 0.1, cx, cy, r);
    tg.addColorStop(0, shade(d.main, 0.22)); tg.addColorStop(1, d.main);
    c.fillStyle = tg; c.fill();
    c.lineWidth = Math.max(0.6, r * 0.04); c.strokeStyle = 'rgba(0,0,0,.5)'; c.stroke();
    // rim spots on the top
    c.fillStyle = d.stripe;
    for (var k = 0; k < 8; k++) {
      var b = k * TAU / 8 + 0.2, px = cx + Math.cos(b) * r * 0.86, py = cy + Math.sin(b) * ry * 0.86;
      c.save(); c.translate(px, py); c.rotate(b); c.scale(1, 0.52);
      c.fillRect(-r * 0.075, -r * 0.16, r * 0.15, r * 0.32);
      c.restore();
    }
    // inner ring + denomination
    c.beginPath(); c.ellipse(cx, cy, r * 0.62, ry * 0.62, 0, 0, TAU);
    c.strokeStyle = d.stripe; c.lineWidth = Math.max(0.7, r * 0.05); c.globalAlpha = 0.9; c.stroke(); c.globalAlpha = 1;
    if (ink) {
      c.save(); c.translate(cx, cy); c.scale(1, 0.55);
      c.font = '800 ' + (r * (d.t.length > 3 ? 0.42 : d.t.length > 2 ? 0.52 : 0.7)) + 'px ' + SANS;
      c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillStyle = d.ink; c.fillText(d.t, 0, r * 0.04);
      c.restore();
    }
  }

  // a column of n chips of one denomination standing on (x, y) = the bottom centre
  function drawColumn(c, x, y, r, d, n) {
    var t = r * 0.27;
    for (var i = 0; i < n; i++) drawChipTop(c, x, y - (i + 1) * t, r, d, t, i === n - 1);
  }

  // the whole stack for an amount, centred on (x, y) (the bottom centre of the pile); returns its height
  function drawChipPile(c, x, y, r, amount) {
    var cols = chipsFor(amount);
    if (!cols.length) return 0;
    // up to 3 columns in the front row; the rest stand behind
    var front = cols.slice(-3), back = cols.slice(0, -3);
    var gap = r * 1.62, tmax = 0;
    function row(list, cy, dx0) {
      var n = list.length;
      for (var i = 0; i < n; i++) {
        var cx = x + dx0 + (i - (n - 1) / 2) * gap;
        drawColumn(c, cx, cy, r, list[i].d, Math.min(list[i].n, 9));
        tmax = Math.max(tmax, Math.min(list[i].n, 9) * r * 0.27 + (y - cy));
      }
    }
    if (back.length) row(back, y - r * 0.62, 0);
    row(front, y, 0);
    return tmax + r * 0.5;
  }

  // ------------------------------------------------------------------ the table (geometry)
  var T = { left: 60, right: 1860, top: 232, sideY: 560, cx: 960, rx: 900, ry: 490, rail: 38, corner: 76 };
  var SEAT_E = { cx: 960, cy: 500, rx: 840, ry: 400 };       // the seats' arc
  var DEALER = { x: 960, cardY: 424, plateY: 252, headY: 98, bannerY: 308, cw: 100, ch: 140, dx: 62 };
  var SHOE_XY = { x: 1535, y: 408 }, TRAY_XY = { x: 407, y: 408 };

  // the table outline inset by `i` px (a "D": a straight edge for the dealer, a half ellipse for the players)
  function tablePath(c, i) {
    var l = T.left + i, r = T.right - i, t = T.top + i, rc = Math.max(10, T.corner - i);
    c.beginPath();
    c.moveTo(l + rc, t); c.lineTo(r - rc, t); c.arcTo(r, t, r, t + rc, rc);
    c.lineTo(r, T.sideY); c.ellipse(T.cx, T.sideY, T.rx - i, T.ry - i, 0, 0, Math.PI, false);
    c.lineTo(l, t + rc); c.arcTo(l, t, l + rc, t, rc); c.closePath();
  }

  // seat anchors, card size and the rest for a table of n seats
  function seatLayout(n) {
    n = clamp(n | 0, 1, 14);
    var span = 785, S = n <= 1 ? 0 : Math.min(214, 2 * span / (n - 1));
    var cw = clamp(Math.round(S * 0.62), 54, 124);
    var L = { n: n, S: S, cw: cw, ch: Math.round(cw * 1.4), R: clamp(Math.round(S * 0.28), 26, 44), seats: [] };
    L.cr = Math.round(L.R * 0.74);                          // chip radius
    L.plateW = Math.min(Math.round(S - 8) || 190, 190); L.plateH = clamp(Math.round(S * 0.2), 27, 36);
    for (var i = 0; i < n; i++) {
      var dx = (i - (n - 1) / 2) * S, f = Math.sqrt(Math.max(0, 1 - Math.pow(dx / SEAT_E.rx, 2)));
      L.seats.push({ x: SEAT_E.cx + dx, y: SEAT_E.cy + SEAT_E.ry * f });
    }
    return L;
  }

  // text along an arc (circle centre cx,cy, radius r): reads left to right along the bottom of the circle,
  // the letters upright towards the centre
  function arcText(c, text, cx, cy, r, spacing, stroke) {
    var i, widths = [], total = 0;
    for (i = 0; i < text.length; i++) { var w = c.measureText(text.charAt(i)).width; widths.push(w); total += w + spacing; }
    total -= spacing;
    var a0 = Math.PI / 2 + total / r / 2, run = 0;
    c.textAlign = 'center'; c.textBaseline = 'alphabetic';
    for (i = 0; i < text.length; i++) {
      var mid = a0 - (run + widths[i] / 2) / r;
      c.save(); c.translate(cx + r * Math.cos(mid), cy + r * Math.sin(mid)); c.rotate(mid - Math.PI / 2);
      if (stroke) c.strokeText(text.charAt(i), 0, 0);
      c.fillText(text.charAt(i), 0, 0); c.restore();
      run += widths[i] + spacing;
    }
  }

  var NOISE = null;
  function noisePattern(c) {
    if (!NOISE) {
      var cv = mk(160, 160), g = cv.getContext('2d'), rnd = mulberry(77);
      for (var i = 0; i < 2600; i++) {
        var v = rnd() < 0.5 ? 0 : 255;
        g.fillStyle = 'rgba(' + v + ',' + v + ',' + v + ',' + (0.025 + rnd() * 0.05).toFixed(3) + ')';
        g.fillRect(rnd() * 160, rnd() * 160, 1 + rnd() * 1.6, 1 + rnd() * 1.6);
      }
      NOISE = cv;
    }
    return c.createPattern(NOISE, 'repeat');
  }

  function rulesLines(rules) {
    rules = rules || {};
    var pays = String(rules.blackjack_pays || '3:2').replace(':', ' TO ');
    return {
      big: 'BLACKJACK PAYS ' + pays,
      mid: rules.dealer_hits_soft_17 ? 'DEALER MUST HIT SOFT 17' : 'DEALER MUST STAND ON ALL 17s',
      low: rules.insurance === false ? 'NO INSURANCE' : 'INSURANCE PAYS 2 TO 1',
      rail: [(rules.double_on === '10-11' ? 'DOUBLE ON 10 OR 11' : rules.double_on === '9-11' ? 'DOUBLE ON 9, 10 OR 11' : 'DOUBLE ON ANY TWO CARDS'),
        (rules.max_hands > 1 ? 'SPLIT TO ' + rules.max_hands + ' HANDS' : 'NO SPLITS'),
        (rules.double_after_split === false ? 'NO DOUBLE AFTER SPLIT' : 'DOUBLE AFTER SPLIT'),
        (rules.surrender && rules.dealer_peeks !== false ? 'LATE SURRENDER' : 'NO SURRENDER')]
    };
  }

  // The static table: shadow, rail, felt (noise + honeycomb), trim, the dealer's tray, the arced rules and the
  // betting circles. Cached per theme / size / rules / seat count.
  function buildFelt(th, k, lay, rules, showRules, title) {
    var cv = mk(BASE_W * k, BASE_H * k), c = cv.getContext('2d');
    c.setTransform(k, 0, 0, k, 0, 0);
    // the table's shadow on whatever is behind it
    c.save(); c.shadowColor = 'rgba(0,0,0,.6)'; c.shadowBlur = 46; c.shadowOffsetY = 20;
    tablePath(c, 0); c.fillStyle = th.rail2; c.fill(); c.restore();
    // rail: padded leather / wood
    tablePath(c, 0);
    var rg = c.createLinearGradient(0, T.top, 0, T.sideY + T.ry);
    rg.addColorStop(0, th.rail1); rg.addColorStop(0.5, th.rail2); rg.addColorStop(1, th.rail1);
    c.fillStyle = rg; c.fill();
    c.lineWidth = 3; c.strokeStyle = th.railHi; tablePath(c, 2); c.stroke();
    c.lineWidth = 2; c.strokeStyle = 'rgba(0,0,0,.45)'; tablePath(c, T.rail - 3); c.stroke();
    c.lineWidth = 1.5; c.strokeStyle = th.railHi; c.globalAlpha = 0.45; tablePath(c, T.rail * 0.5); c.stroke(); c.globalAlpha = 1;
    // felt
    tablePath(c, T.rail);
    var fg = c.createRadialGradient(960, 600, 60, 960, 640, 1000);
    fg.addColorStop(0, th.feltA); fg.addColorStop(0.55, th.feltB); fg.addColorStop(1, th.feltC);
    c.fillStyle = fg; c.fill();
    c.save(); tablePath(c, T.rail); c.clip();
    c.fillStyle = noisePattern(c); c.fillRect(0, 0, BASE_W, BASE_H);
    // honeycomb: the Hex theme
    var hr = 54, hh = hr * Math.sqrt(3), rnd = mulberry(5);
    c.lineWidth = 1.6; c.strokeStyle = th.hex;
    for (var row = -1; row < BASE_H / hh + 2; row++) {
      for (var col = -1; col < BASE_W / (hr * 1.5) + 2; col++) {
        var hx = col * hr * 1.5, hy = row * hh + (col % 2 ? hh / 2 : 0);
        hexPath(c, hx, hy, hr - 1, 0); c.stroke();
        var d = Math.hypot(hx - 960, hy - 640);
        if (d > 520 && rnd() < 0.07) { hexPath(c, hx, hy, hr - 5, 0); c.fillStyle = th.hexHi; c.globalAlpha = 0.35; c.fill(); c.globalAlpha = 1; c.lineWidth = 2.2; c.strokeStyle = th.hexHi; c.stroke(); c.lineWidth = 1.6; c.strokeStyle = th.hex; }
      }
    }
    // vignette
    var vg = c.createRadialGradient(960, 620, 380, 960, 680, 1020);
    vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(0,0,0,.5)');
    c.fillStyle = vg; c.fillRect(0, 0, BASE_W, BASE_H);
    // soft light from the dealer's side
    var lg = c.createRadialGradient(960, 330, 20, 960, 360, 560);
    lg.addColorStop(0, 'rgba(255,255,230,.12)'); lg.addColorStop(1, 'rgba(255,255,230,0)');
    c.fillStyle = lg; c.fillRect(0, 0, BASE_W, BASE_H);
    c.restore();
    // trim
    if (th.glow) { c.save(); c.shadowColor = th.trim; c.shadowBlur = 18; }
    c.lineWidth = 3; c.strokeStyle = th.trim; tablePath(c, T.rail + 16); c.stroke();
    if (th.glow) c.restore();
    c.lineWidth = 1.4; c.strokeStyle = th.trimDim; tablePath(c, T.rail + 25); c.stroke();
    // the dealer's chip racks
    rack(c, 560, 280, th); rack(c, 1360, 280, th);
    // the rules, arced like the printing on a real felt
    if (showRules) {
      var rl = rulesLines(rules), cx = 960, cy = 118;
      c.save();
      c.fillStyle = th.ink; c.strokeStyle = 'rgba(0,0,0,.35)'; c.lineWidth = 3; c.lineJoin = 'round';
      c.font = '700 25px ' + SERIF; arcText(c, rl.mid, cx, cy, 462, 3.2, true);
      c.fillStyle = th.accent; c.font = '800 52px ' + SERIF; c.lineWidth = 5; arcText(c, rl.big, cx, cy, 540, 5, true);
      c.fillStyle = th.ink; c.font = '700 26px ' + SERIF; c.lineWidth = 3; arcText(c, rl.low, cx, cy, 598, 4, true);
      c.restore();
      // the small print along the bottom rail
      c.save(); c.font = '700 14px ' + SANS; c.fillStyle = th.trim; c.globalAlpha = 0.85; c.textAlign = 'center';
      c.fillText(rl.rail.join('   ·   '), 960, T.sideY + T.ry - 15);
      c.restore();
    }
    // the table's plaque on the rail (your branding)
    if (title) plaque(c, 330, T.top + 20, 330, 32, title, th);
    // betting circles
    for (var i = 0; i < lay.seats.length; i++) {
      var s = lay.seats[i], R = lay.R;
      c.beginPath(); c.arc(s.x, s.y, R, 0, TAU); c.fillStyle = 'rgba(0,0,0,.20)'; c.fill();
      c.lineWidth = 3; c.strokeStyle = th.trimDim; c.stroke();
      c.beginPath(); c.arc(s.x, s.y, R - 6, 0, TAU); c.lineWidth = 1.2; c.strokeStyle = 'rgba(255,255,255,.20)'; c.stroke();
    }
    return cv;
  }

  function plaque(c, x, y, w, h, text, th) {
    c.save();
    rrect(c, x - w / 2, y - h / 2, w, h, 8);
    var g = c.createLinearGradient(0, y - h / 2, 0, y + h / 2);
    g.addColorStop(0, shade(th.trim, 0.25)); g.addColorStop(0.5, th.trim); g.addColorStop(1, shade(th.trim, -0.35));
    c.fillStyle = g; c.fill(); c.lineWidth = 1.5; c.strokeStyle = 'rgba(0,0,0,.55)'; c.stroke();
    rrect(c, x - w / 2 + 3, y - h / 2 + 3, w - 6, h - 6, 6); c.lineWidth = 1; c.strokeStyle = 'rgba(0,0,0,.28)'; c.stroke();
    c.fillStyle = 'rgba(30,18,6,.92)'; c.textAlign = 'center'; c.textBaseline = 'middle';
    var t = fit(c, String(text).toUpperCase(), '800 %spx ' + SERIF, w - 28, 22, 11);
    c.fillText(t, x, y + 1.5);
    c.restore();
  }

  // "TEST GAME · NO COINS": a red plaque on the right of the rail (the table's own plaque is on the left)
  function testPlaque(c, x, y, w, h) {
    c.save();
    rrect(c, x - w / 2, y - h / 2, w, h, 8);
    var g = c.createLinearGradient(0, y - h / 2, 0, y + h / 2);
    g.addColorStop(0, '#e2574b'); g.addColorStop(0.5, '#b8342b'); g.addColorStop(1, '#7c1f19');
    c.fillStyle = g; c.fill(); c.lineWidth = 1.5; c.strokeStyle = 'rgba(0,0,0,.55)'; c.stroke();
    rrect(c, x - w / 2 + 3, y - h / 2 + 3, w - 6, h - 6, 6); c.lineWidth = 1; c.strokeStyle = 'rgba(255,255,255,.35)'; c.stroke();
    c.fillStyle = '#fff'; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.font = '800 17px ' + SANS;
    c.fillText('TEST GAME · NO COINS', x, y + 1);
    c.restore();
  }

  // a chip tray at the dealer's side of the table (decoration): rows of chips seen from the front
  function rack(c, x, y, th) {
    var w = 190, h = 56;
    c.save();
    rrect(c, x - w / 2, y, w, h, 7);
    var g = c.createLinearGradient(0, y, 0, y + h);
    g.addColorStop(0, '#9aa3ae'); g.addColorStop(0.5, '#5b6470'); g.addColorStop(1, '#2d333c');
    c.fillStyle = g; c.fill(); c.lineWidth = 2; c.strokeStyle = 'rgba(0,0,0,.6)'; c.stroke();
    var order = [DENOMS[9], DENOMS[8], DENOMS[7], DENOMS[6], DENOMS[5]], cw = 28;
    for (var i = 0; i < 5; i++) {
      var cx = x - w / 2 + 16 + i * 32, d = order[i];
      for (var j = 0; j < 7; j++) {
        var cy = y + h - 9 - j * 5.4;
        rrect(c, cx - cw / 2 + 2, cy - 4, cw - 4, 6, 2);
        c.fillStyle = d.main; c.fill();
        c.fillStyle = d.stripe; c.fillRect(cx - 9, cy - 3.5, 3, 5); c.fillRect(cx + 5, cy - 3.5, 3, 5);
      }
    }
    // a glossy edge over the front
    rrect(c, x - w / 2, y + h - 18, w, 18, 5); c.fillStyle = 'rgba(20,24,30,.55)'; c.fill();
    c.restore();
  }

  // ------------------------------------------------------------------ Hex, the dealer
  // A bust: vest, bow tie, a green eyeshade with the hexagon on it. (cx, cy) = the middle of the head; s = scale.
  // st: {blink 0..1, mouth 0..1 (open), smile 0..1, brow -1..1, look -1..1}
  function drawDealer(c, cx, cy, s, st, th) {
    st = st || {};
    c.save(); c.translate(cx, cy); c.scale(s, s);
    var i, skin = '#e9bd98', skinD = '#c98f6a';
    // jacket
    c.beginPath(); c.moveTo(-190, 260); c.bezierCurveTo(-186, 170, -160, 118, -96, 104); c.lineTo(-34, 90); c.lineTo(34, 90);
    c.lineTo(96, 104); c.bezierCurveTo(160, 118, 186, 170, 190, 260); c.closePath();
    var jg = c.createLinearGradient(0, 90, 0, 260); jg.addColorStop(0, '#2b2e3c'); jg.addColorStop(1, '#101118');
    c.fillStyle = jg; c.fill(); c.lineWidth = 2; c.strokeStyle = 'rgba(255,255,255,.14)'; c.stroke();
    // shirt
    c.beginPath(); c.moveTo(-36, 88); c.lineTo(36, 88); c.lineTo(0, 232); c.closePath();
    var sg = c.createLinearGradient(-36, 90, 36, 230); sg.addColorStop(0, '#ffffff'); sg.addColorStop(1, '#d9dce6');
    c.fillStyle = sg; c.fill();
    // vest
    c.beginPath(); c.moveTo(-104, 108); c.lineTo(-36, 94); c.lineTo(0, 196); c.lineTo(36, 94); c.lineTo(104, 108);
    c.lineTo(100, 262); c.lineTo(-100, 262); c.closePath();
    var vg = c.createLinearGradient(-100, 100, 100, 260); vg.addColorStop(0, '#34304a'); vg.addColorStop(1, '#14121f');
    c.fillStyle = vg; c.fill(); c.lineWidth = 2; c.strokeStyle = rgba('#e6c06c', 0.5); c.stroke();
    for (i = 0; i < 3; i++) { c.beginPath(); c.arc(0, 150 + i * 28, 5, 0, TAU); c.fillStyle = '#e6c06c'; c.fill(); c.lineWidth = 1; c.strokeStyle = 'rgba(0,0,0,.5)'; c.stroke(); }
    // a hex pin on the lapel
    hexPath(c, -62, 140, 9, Math.PI / 6); c.fillStyle = '#e6c06c'; c.fill(); c.lineWidth = 1.4; c.strokeStyle = 'rgba(0,0,0,.6)'; c.stroke();
    // neck
    c.beginPath(); c.moveTo(-22, 50); c.lineTo(-22, 92); c.quadraticCurveTo(0, 104, 22, 92); c.lineTo(22, 50); c.closePath();
    var ng = c.createLinearGradient(0, 50, 0, 100); ng.addColorStop(0, skinD); ng.addColorStop(1, '#b57e5a');
    c.fillStyle = ng; c.fill();
    // collar
    c.beginPath(); c.moveTo(-26, 82); c.lineTo(-2, 100); c.lineTo(-30, 118); c.closePath(); c.fillStyle = '#fff'; c.fill(); c.lineWidth = 1; c.strokeStyle = 'rgba(0,0,0,.25)'; c.stroke();
    c.beginPath(); c.moveTo(26, 82); c.lineTo(2, 100); c.lineTo(30, 118); c.closePath(); c.fillStyle = '#fff'; c.fill(); c.stroke();
    // bow tie
    var bt = '#b8233b';
    c.beginPath(); c.moveTo(0, 100); c.quadraticCurveTo(-18, 84, -40, 90); c.quadraticCurveTo(-34, 104, -40, 118); c.quadraticCurveTo(-18, 116, 0, 104); c.closePath();
    c.fillStyle = bt; c.fill(); c.lineWidth = 1.4; c.strokeStyle = 'rgba(0,0,0,.45)'; c.stroke();
    c.beginPath(); c.moveTo(0, 100); c.quadraticCurveTo(18, 84, 40, 90); c.quadraticCurveTo(34, 104, 40, 118); c.quadraticCurveTo(18, 116, 0, 104); c.closePath();
    c.fill(); c.stroke();
    c.beginPath(); c.ellipse(0, 102, 8, 10, 0, 0, TAU); c.fillStyle = shade(bt, -0.2); c.fill(); c.stroke();
    // ears
    c.fillStyle = skinD;
    c.beginPath(); c.ellipse(-52, 8, 9, 14, 0, 0, TAU); c.fill(); c.beginPath(); c.ellipse(52, 8, 9, 14, 0, 0, TAU); c.fill();
    // head
    c.beginPath(); c.ellipse(0, 0, 52, 62, 0, 0, TAU);
    var hg = c.createRadialGradient(-16, -22, 8, 0, 4, 76); hg.addColorStop(0, '#f6d2b2'); hg.addColorStop(0.7, skin); hg.addColorStop(1, '#cf9a74');
    c.fillStyle = hg; c.fill(); c.lineWidth = 1.5; c.strokeStyle = 'rgba(90,50,30,.35)'; c.stroke();
    // hair
    c.beginPath(); c.moveTo(-52, -4); c.bezierCurveTo(-60, -50, -34, -72, 0, -72); c.bezierCurveTo(34, -72, 60, -50, 52, -4);
    c.bezierCurveTo(46, -26, 30, -40, 0, -42); c.bezierCurveTo(-30, -40, -46, -26, -52, -4); c.closePath();
    c.fillStyle = '#2a1b14'; c.fill();
    // the eyeshade: strap, then the translucent visor with the hexagon
    c.lineCap = 'round'; c.lineWidth = 7; c.strokeStyle = '#17171d';
    c.beginPath(); c.moveTo(-54, -22); c.quadraticCurveTo(0, -50, 54, -22); c.stroke();
    var vis = th && th.visor ? th.visor : '#2f9d66';
    c.beginPath(); c.moveTo(-68, -6); c.quadraticCurveTo(0, -56, 68, -6); c.quadraticCurveTo(0, -20, -68, -6); c.closePath();
    var vgr = c.createLinearGradient(0, -48, 0, -10); vgr.addColorStop(0, rgba(shade(vis, 0.25).indexOf('#') === 0 ? vis : vis, 0.95)); vgr.addColorStop(1, rgba(vis, 0.7));
    c.fillStyle = vgr; c.fill(); c.lineWidth = 2; c.strokeStyle = 'rgba(0,0,0,.55)'; c.stroke();
    hexPath(c, 0, -27, 11, Math.PI / 6); c.fillStyle = '#f0c75e'; c.fill(); c.lineWidth = 1.6; c.strokeStyle = '#6b4a00'; c.stroke();
    c.lineWidth = 1.2; c.beginPath(); c.moveTo(0, -27); c.lineTo(0, -37.5); c.moveTo(0, -27); c.lineTo(-9.1, -21.7); c.moveTo(0, -27); c.lineTo(9.1, -21.7); c.stroke();
    // eyes
    var blink = clamp(st.blink || 0, 0, 1), look = clamp(st.look || 0, -1, 1);
    for (var e = -1; e <= 1; e += 2) {
      var ex = e * 21, ey = 10;
      c.beginPath(); c.ellipse(ex, ey, 11, 7.5 * (1 - blink * 0.92), 0, 0, TAU); c.fillStyle = '#fbfbf8'; c.fill();
      c.lineWidth = 1.2; c.strokeStyle = 'rgba(60,30,20,.55)'; c.stroke();
      if (blink < 0.7) {
        c.beginPath(); c.arc(ex + look * 3.5, ey + 0.5, 4.8, 0, TAU); c.fillStyle = '#3a2418'; c.fill();
        c.beginPath(); c.arc(ex + look * 3.5 - 1.4, ey - 1.6, 1.5, 0, TAU); c.fillStyle = 'rgba(255,255,255,.9)'; c.fill();
      }
      // brows
      var br = clamp(st.brow || 0, -1, 1);
      c.lineWidth = 4.2; c.strokeStyle = '#2a1b14';
      c.beginPath(); c.moveTo(ex - e * 12, -4 - br * 2 + e * 1.5 * (br > 0 ? -1 : 1)); c.lineTo(ex + e * 11, -3 - br * 8 * (br > 0 ? 1 : 0.6) + e * 0);
      c.stroke();
    }
    // nose and cheeks
    c.lineWidth = 2; c.strokeStyle = 'rgba(120,70,45,.5)'; c.beginPath(); c.moveTo(-3, 18); c.quadraticCurveTo(-8, 30, 0, 32); c.quadraticCurveTo(7, 32, 4, 26); c.stroke();
    c.fillStyle = 'rgba(230,110,100,.22)'; c.beginPath(); c.arc(-34, 30, 8, 0, TAU); c.fill(); c.beginPath(); c.arc(34, 30, 8, 0, TAU); c.fill();
    // mouth
    var sm = clamp(st.smile == null ? 0.6 : st.smile, 0, 1), op = clamp(st.mouth || 0, 0, 1);
    c.beginPath(); c.moveTo(-16, 42);
    c.quadraticCurveTo(0, 42 + 6 + sm * 12 + op * 10, 16, 42);
    if (op > 0.05) { c.quadraticCurveTo(0, 42 - 1 + op * 3, -16, 42); c.closePath(); c.fillStyle = '#6b1f26'; c.fill(); }
    c.lineWidth = 2.6; c.strokeStyle = '#7a3a2c'; c.lineCap = 'round'; c.stroke();
    if (op > 0.15) {
      c.fillStyle = '#fff'; c.fillRect(-9, 42 + 0.5, 18, 3.2);
    }
    // a glint on the forehead / cheek
    c.fillStyle = 'rgba(255,255,255,.10)'; c.beginPath(); c.ellipse(-20, -2, 14, 20, -0.4, 0, TAU); c.fill();
    c.restore();
  }

  // ------------------------------------------------------------------ the shoe and the discard tray
  // The shoe from above: the stack of cards left (a bar that shortens as they are dealt), the cut card in it,
  // the mouth on the left. info: {left, size, to_cut, cut_passed, decks}
  function drawShoe(c, x, y, th, info, flash) {
    var W = 292, H = 100, size = Math.max(1, info.size), frac = clamp(info.left / size, 0, 1);
    c.save(); c.translate(x, y); c.rotate(-0.05);
    c.save(); c.shadowColor = 'rgba(0,0,0,.55)'; c.shadowBlur = 18; c.shadowOffsetY = 10;
    rrect(c, -W / 2, -H / 2, W, H, 18);
    var bg = c.createLinearGradient(0, -H / 2, 0, H / 2); bg.addColorStop(0, '#3a404c'); bg.addColorStop(1, '#14161d');
    c.fillStyle = bg; c.fill(); c.restore();
    c.lineWidth = 2; c.strokeStyle = 'rgba(190,200,215,.5)'; rrect(c, -W / 2, -H / 2, W, H, 18); c.stroke();
    // the lane
    var lx = -W / 2 + 18, lw = W - 40, ly = -24, lh = 48;
    rrect(c, lx, ly, lw, lh, 7); c.fillStyle = '#0a0b10'; c.fill(); c.lineWidth = 1.5; c.strokeStyle = 'rgba(255,255,255,.12)'; c.stroke();
    var L = lw * frac, sx = lx + lw - L;
    if (L > 1) {
      c.save(); rrect(c, lx, ly, lw, lh, 7); c.clip();
      c.fillStyle = '#f5f2ea'; c.fillRect(sx, ly + 3, L, lh - 6);
      c.fillStyle = 'rgba(0,0,0,.18)';
      for (var px = sx + 2.4; px < sx + L; px += 2.4) c.fillRect(px, ly + 3, 0.8, lh - 6);
      var sh = c.createLinearGradient(0, ly, 0, ly + lh); sh.addColorStop(0, 'rgba(255,255,255,.35)'); sh.addColorStop(0.5, 'rgba(255,255,255,0)'); sh.addColorStop(1, 'rgba(0,0,0,.3)');
      c.fillStyle = sh; c.fillRect(sx, ly + 3, L, lh - 6);
      // the face-down card showing at the mouth
      c.fillStyle = th.backA; c.fillRect(sx, ly + 5, 5, lh - 10);
      // the cut card
      var cutX = info.cut_passed ? sx + 2 : sx + clamp(info.to_cut / size, 0, 1) * lw;
      cutX = clamp(cutX, sx + 2, lx + lw - 3);
      c.fillStyle = (flash && info.cut_passed) ? '#ff5a3c' : '#ffcb2f';
      c.fillRect(cutX - 2.2, ly - 1, 4.4, lh + 2);
      c.restore();
    }
    // the glass over it
    rrect(c, lx, ly, lw, lh, 7);
    var gg = c.createLinearGradient(0, ly, 0, ly + lh); gg.addColorStop(0, 'rgba(255,255,255,.22)'); gg.addColorStop(0.45, 'rgba(255,255,255,.04)'); gg.addColorStop(1, 'rgba(255,255,255,.1)');
    c.fillStyle = gg; c.fill();
    // the badge
    c.font = '800 14px ' + SANS; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillStyle = th.accent; c.fillText(info.decks + (info.decks === 1 ? ' DECK' : ' DECKS'), -W / 2 + 58, 36);
    c.fillStyle = 'rgba(255,255,255,.85)'; c.fillText(info.left + ' CARDS', 0, 36);
    c.fillStyle = info.cut_passed ? '#ff8a6a' : 'rgba(255,255,255,.62)';
    c.fillText(info.cut_passed ? 'SHUFFLE NEXT' : (info.to_cut + ' TO CUT'), W / 2 - 62, 36);
    c.restore();
  }

  function drawTray(c, x, y, th, info) {
    var W = 210, H = 82, size = Math.max(1, info.size || 1), frac = clamp((info.discards || 0) / size, 0, 1);
    c.save(); c.translate(x, y); c.rotate(0.04);
    c.save(); c.shadowColor = 'rgba(0,0,0,.5)'; c.shadowBlur = 14; c.shadowOffsetY = 8;
    rrect(c, -W / 2, -H / 2, W, H, 14);
    var bg = c.createLinearGradient(0, -H / 2, 0, H / 2); bg.addColorStop(0, '#454b58'); bg.addColorStop(1, '#171a21');
    c.fillStyle = bg; c.fill(); c.restore();
    c.lineWidth = 2; c.strokeStyle = 'rgba(190,200,215,.45)'; rrect(c, -W / 2, -H / 2, W, H, 14); c.stroke();
    var lx = -W / 2 + 14, lw = W - 28, ly = -22, lh = 40;
    rrect(c, lx, ly, lw, lh, 6); c.fillStyle = '#090a0e'; c.fill();
    var L = lw * frac;
    if (L > 1) {
      c.save(); rrect(c, lx, ly, lw, lh, 6); c.clip();
      var g = c.createLinearGradient(0, ly, 0, ly + lh); g.addColorStop(0, shade(th.backA, 0.25)); g.addColorStop(1, th.backB);
      c.fillStyle = g; c.fillRect(lx, ly + 3, L, lh - 6);
      c.fillStyle = 'rgba(255,255,255,.22)';
      for (var px = lx + 2.6; px < lx + L; px += 2.6) c.fillRect(px, ly + 3, 0.8, lh - 6);
      c.restore();
    }
    rrect(c, lx, ly, lw, lh, 6); var gg = c.createLinearGradient(0, ly, 0, ly + lh); gg.addColorStop(0, 'rgba(255,255,255,.2)'); gg.addColorStop(1, 'rgba(255,255,255,.04)');
    c.fillStyle = gg; c.fill();
    c.font = '800 14px ' + SANS; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillStyle = 'rgba(255,255,255,.8)';
    c.fillText('DISCARDS  ' + (info.discards || 0), 0, 30);
    c.restore();
  }

  // ------------------------------------------------------------------ sound (synthesized: no files)
  function Sfx() { this.ctx = null; this.vol = 0.6; this.last = {}; }
  Sfx.prototype._ac = function () {
    if (!this.ctx) {
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) return null;
      try { this.ctx = new AC(); } catch (e) { return null; }
    }
    if (this.ctx.state === 'suspended') { try { this.ctx.resume(); } catch (e) {} }
    return this.ctx;
  };
  Sfx.prototype._noise = function (ac, dur, f0, f1, q, gain, when) {
    var n = Math.max(1, Math.floor(ac.sampleRate * dur)), buf = ac.createBuffer(1, n, ac.sampleRate), d = buf.getChannelData(0);
    for (var i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 1.6);
    var src = ac.createBufferSource(), bp = ac.createBiquadFilter(), g = ac.createGain();
    src.buffer = buf; bp.type = 'bandpass'; bp.Q.value = q;
    bp.frequency.setValueAtTime(f0, when); bp.frequency.exponentialRampToValueAtTime(Math.max(60, f1), when + dur);
    g.gain.value = gain * this.vol;
    src.connect(bp); bp.connect(g); g.connect(ac.destination); src.start(when);
  };
  Sfx.prototype._tone = function (ac, type, f0, f1, dur, gain, when) {
    var o = ac.createOscillator(), g = ac.createGain();
    o.type = type; o.frequency.setValueAtTime(f0, when); if (f1 !== f0) o.frequency.exponentialRampToValueAtTime(Math.max(30, f1), when + dur);
    g.gain.setValueAtTime(0.0001, when); g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain * this.vol), when + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    o.connect(g); g.connect(ac.destination); o.start(when); o.stop(when + dur + 0.02);
  };
  Sfx.prototype.play = function (name) {
    var ac = this._ac();
    if (!ac || this.vol <= 0) return;
    var t = ac.currentTime + 0.005, i;
    switch (name) {
      case 'card': this._noise(ac, 0.11, 4200, 1500, 0.9, 0.5, t); this._tone(ac, 'triangle', 300, 120, 0.05, 0.05, t); break;
      case 'flip': this._noise(ac, 0.06, 3000, 1800, 1.4, 0.4, t); this._tone(ac, 'square', 1400, 900, 0.025, 0.03, t); break;
      case 'chip': this._tone(ac, 'sine', 2300, 1700, 0.05, 0.16, t); this._tone(ac, 'sine', 3400, 2600, 0.035, 0.09, t + 0.012); this._noise(ac, 0.03, 5200, 3200, 2, 0.12, t); break;
      case 'chips': for (i = 0; i < 5; i++) { this._tone(ac, 'sine', 2000 + i * 230, 1500, 0.045, 0.13, t + i * 0.045); this._noise(ac, 0.025, 5000, 3000, 2, 0.09, t + i * 0.045); } break;
      case 'shuffle': for (i = 0; i < 14; i++) this._noise(ac, 0.07, 3800 - i * 60, 1600, 0.8, 0.35, t + i * 0.11 + Math.random() * 0.03); break;
      case 'win': [523, 659, 784].forEach(function (f, j) { this._tone(ac, 'triangle', f, f, 0.22, 0.2, t + j * 0.09); this._tone(ac, 'sine', f * 2, f * 2, 0.18, 0.06, t + j * 0.09); }, this); break;
      case 'blackjack': [523, 659, 784, 1047, 1319].forEach(function (f, j) { this._tone(ac, 'triangle', f, f, 0.3, 0.2, t + j * 0.085); this._tone(ac, 'sine', f * 2, f * 2, 0.25, 0.07, t + j * 0.085); }, this); break;
      case 'lose': this._tone(ac, 'sine', 160, 70, 0.32, 0.3, t); break;
      case 'bust': this._tone(ac, 'sawtooth', 330, 110, 0.4, 0.14, t); this._tone(ac, 'sine', 110, 55, 0.35, 0.28, t + 0.05); break;
      case 'push': this._tone(ac, 'triangle', 392, 392, 0.14, 0.15, t); this._tone(ac, 'triangle', 392, 392, 0.14, 0.15, t + 0.16); break;
      case 'tick': this._tone(ac, 'square', 1500, 1500, 0.03, 0.07, t); break;
      case 'go': this._tone(ac, 'triangle', 660, 880, 0.12, 0.16, t); break;
    }
  };

  // ------------------------------------------------------------------ small drawing helpers
  function pill(c, x, y, text, o) {
    // a rounded label centred on (x, y); o: {h, font, bg, fg, border, minW, pad, glow}
    var h = o.h || 24, pad = o.pad == null ? 10 : o.pad;
    c.font = o.font || ('800 ' + Math.round(h * 0.62) + 'px ' + SANS);
    var w = Math.max(o.minW || 0, c.measureText(text).width + pad * 2);
    if (o.maxW && w > o.maxW) { // shrink the text to the width
      var f = Math.max(9, Math.floor(h * 0.62 * (o.maxW - pad * 2) / (w - pad * 2)));
      c.font = '800 ' + f + 'px ' + SANS; w = Math.max(o.minW || 0, c.measureText(text).width + pad * 2);
    }
    c.save();
    if (o.glow) { c.shadowColor = o.glow; c.shadowBlur = 14; }
    rrect(c, x - w / 2, y - h / 2, w, h, h / 2);
    c.fillStyle = o.bg || 'rgba(8,12,18,.82)'; c.fill();
    c.shadowBlur = 0;
    if (o.border) { c.lineWidth = o.bw || 1.6; c.strokeStyle = o.border; c.stroke(); }
    c.fillStyle = o.fg || '#fff'; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText(text, x, y + h * 0.04);
    c.restore();
    return w;
  }

  // ------------------------------------------------------------------ the instance
  var FLY = 420;
  var SHOE_MOUTH = { x: SHOE_XY.x - 140, y: SHOE_XY.y - 4 };
  var STATUS_WORD = { hit: 'HIT', stand: 'STAND', double: 'DOUBLE', split: 'SPLIT', surrender: 'SURRENDER' };
  var STATUS_COL = { hit: '#46c96f', stand: '#e5645a', double: '#f0c75e', split: '#5aa7ff', surrender: '#aab3c4' };
  var PHASE_TEXT = { betting: 'PLACE YOUR BETS', dealing: 'DEALING', insurance: 'INSURANCE?', action: 'HIT OR STAND?',
    resolve: 'DRAWING CARDS', dealer: 'DEALER PLAYS', settle: 'PAYING OUT', over: 'TABLE CLOSED' };

  function BJ(container, config, opts) {
    opts = opts || {};
    this.box = container;
    this.cfg = merge(DEFAULTS, config);
    this.soundOn = opts.sound !== false;
    this.sfx = this.soundOn ? new Sfx() : null;
    this.canvas = root.document.createElement('canvas');
    this.canvas.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;display:block';
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');
    this.k = 1; this.st = null; this.g = null; this.idle = null; this.at = now();
    this.sprites = {}; this.spriteN = 0; this.cardPos = {}; this.chipAt = {}; this.ghosts = []; this.drawn = [];
    this.done = {}; this.lastT = now(); this.lay = seatLayout(10); this.layN = 10; this.feltKey = ''; this.felt = null;
    this.skey = ''; this.sched = {}; this.entryFrom = {}; this.seenSay = null; this.sayAt = 0; this.fx = [];
    this.setConfig(this.cfg);
    this.resize();
    var self = this;
    this._loop = function (t) { if (self.dead) return; try { self._frame(t); } catch (e) { self._err(e); } self._raf = raf(self._loop); };
    this._raf = raf(this._loop);
  }
  var P = BJ.prototype;

  P._err = function (e) { if (!this._errAt || now() - this._errAt > 5000) { this._errAt = now(); if (root.console) root.console.error('[blackjack] frame error:', e); } };

  P.setConfig = function (cfg) {
    this.cfg = merge(DEFAULTS, cfg);
    this.th = THEMES[this.cfg.theme] || THEMES.classic;
    if (this.sfx) this.sfx.vol = clamp(+this.cfg.sfx_volume || 0, 0, 1) * (this.cfg.sfx ? 1 : 0);
    this.feltKey = ''; this.sprites = {}; this.spriteN = 0;
  };

  P.resize = function () {
    var r = this.box.getBoundingClientRect ? this.box.getBoundingClientRect() : { width: BASE_W };
    var dpr = root.devicePixelRatio || 1;
    var w = r.width > 4 ? r.width : BASE_W;
    var k = clamp(w * dpr / BASE_W, 0.25, 2.5), cw = Math.round(BASE_W * k), ch = Math.round(BASE_H * k);
    if (cw === this.canvas.width && ch === this.canvas.height && k === this.k) return;   // resizing clears the canvas
    this.k = k; this.canvas.width = cw; this.canvas.height = ch;
    this.feltKey = ''; this.sprites = {}; this.spriteN = 0;
  };

  P.setState = function (st) {
    var prev = this.g;
    this.st = st && typeof st === 'object' ? st : null;
    this.g = this.st && this.st.game && typeof this.st.game === 'object' ? this.st.game : null;
    this.idle = this.st && this.st.idle || null;
    this.at = now();
    var g = this.g;
    if (g) {
      var n = clamp(+g.seat_count || (g.seats && g.seats.length) || 10, 1, 14);
      if (n !== this.layN) { this.layN = n; this.lay = seatLayout(n); this.feltKey = ''; }
      if (!prev || prev.id !== g.id) { this.cardPos = {}; this.chipAt = {}; this.ghosts = []; this.done = {}; }
      if (prev && prev.id === g.id && prev.phase === 'settle' && g.phase === 'betting') this._sweep();
      if (!prev || prev.hand_no !== g.hand_no || prev.phase !== g.phase || prev.round !== g.round) this._schedule(prev);
      if (g.say && (!this.seenSay || this.seenSay !== g.say.id)) { this.seenSay = g.say.id; this.sayAt = now(); }
    } else {
      var seats = this.idle && +this.idle.seats;
      if (seats && seats !== this.layN) { this.layN = clamp(seats, 1, 14); this.lay = seatLayout(this.layN); this.feltKey = ''; }
      this.sched = {}; this.skey = ''; this.cardPos = {}; this.chipAt = {};
    }
  };

  P.reset = function () { this.st = null; this.g = null; this.idle = null; this.cardPos = {}; this.chipAt = {}; this.ghosts = []; this.done = {}; this.skey = ''; this.sched = {}; this.fx = []; };
  P.destroy = function () {
    this.dead = true; caf(this._raf);
    if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
    if (this.sfx && this.sfx.ctx) { try { this.sfx.ctx.close(); } catch (e) {} }
  };

  // ms since the phase started / until it ends (the STATE's clock, aged by the time it sat here)
  P._elapsed = function (t) { var g = this.g; return g ? (+g.elapsed_ms || 0) + (t - this.at) : 0; };
  P._left = function (t) { var g = this.g; return g && g.ends_in_ms != null ? Math.max(0, (+g.ends_in_ms || 0) - (t - this.at)) : null; };
  P._snd = function (name, key) {
    if (!this.sfx) return;
    if (key) { if (this.done[key]) return; this.done[key] = 1; }
    try { this.sfx.play(name); } catch (e) {}
  };
  // a one-shot effect: fires when its time has come, but never replays an old one on a late join
  P._event = function (key, due, el, name) {
    if (this.done[key] || el < due) return false;
    this.done[key] = 1;
    if (el - due < 600 && name) this._snd(name);
    return true;
  };

  // which card of the dealing phase leaves the shoe when: key (uid:card / d:card) -> its order k
  P._schedule = function (prev) {
    var g = this.g, d = g.deal;
    this.skey = g.id + '|' + g.hand_no + '|' + g.phase + '|' + g.round;
    this.sched = {}; this.entryFrom = {};
    this.moveAt = null;
    if (!d || !d.order) return;
    for (var k = 0; k < d.order.length; k++) {
      var o = d.order[k], key;
      if (o[0] === 0) key = 'd:' + o[2];
      else { var seat = g.seats[o[0] - 1], h = seat && seat.hands && seat.hands[o[1]]; if (!h) continue; key = h.uid + ':' + o[2]; }
      this.sched[key] = k;
    }
    // a split: the second card of the pair slides over to its new hand
    var mv = d.moves || [];
    for (var m = 0; m < mv.length; m++) {
      var s2 = g.seats[mv[m][0] - 1];
      if (!s2) continue;
      var from = s2.hands[mv[m][1]], to = s2.hands[mv[m][2]];
      if (from && to && this.cardPos[from.uid + ':1']) this.entryFrom[to.uid + ':0'] = { x: this.cardPos[from.uid + ':1'].x, y: this.cardPos[from.uid + ':1'].y };
    }
  };

  // the cards of the last hand go to the discard tray
  P._sweep = function () {
    var list = this.drawn, tn = now(), i;
    for (i = 0; i < list.length; i++) {
      var d = list[i];
      this.ghosts.push({ code: d.code, x: d.x, y: d.y, w: d.w, h: d.h, rot: d.rot || 0, at: tn + 60 + i * 28, dur: 520 });
    }
    this._snd('card');
  };

  // ---- the static table, cached
  P._feltCanvas = function () {
    var g = this.g, rules = (g && g.rules) || (this.idle && this.idle.rules) || DEFAULTS;
    var key = [this.cfg.theme, this.k, this.layN, this.cfg.show_rules, this.cfg.title, rules.blackjack_pays, rules.dealer_hits_soft_17,
      rules.insurance, rules.double_on, rules.max_hands, rules.double_after_split, rules.surrender, rules.dealer_peeks].join('|');
    if (key !== this.feltKey || !this.felt) {
      this.felt = buildFelt(this.th, this.k, this.lay, rules, !!this.cfg.show_rules, String(this.cfg.title || '').trim());
      this.feltKey = key;
    }
    return this.felt;
  };

  // ---- card sprites
  // A sprite is the card with its soft shadow baked in (a margin of CARD_M px around it): drawing a shadow per card
  // per frame is the most expensive thing the table does. A card with a glow uses the plain sprite (its own shadow).
  var CARD_M = 10;
  P._sprite = function (code, w, h, plain) {
    w = Math.round(w); h = Math.round(h);
    var key = (code || 'back') + '|' + w + 'x' + h + '|' + this.cfg.theme + '|' + this.k.toFixed(2) + (plain ? '|p' : ''), s = this.sprites[key];
    if (s) return s;
    if (this.spriteN > 420) { this.sprites = {}; this.spriteN = 0; }
    var k = this.k, m = plain ? 0 : CARD_M, cv = mk((w + 2 * m) * k + 2, (h + 2 * m) * k + 2), c = cv.getContext('2d');
    c.setTransform(k, 0, 0, k, m * k, m * k);
    if (!plain) {
      c.save(); c.shadowColor = 'rgba(0,0,0,.42)'; c.shadowBlur = 7 * k; c.shadowOffsetY = 3 * k;
      rrect(c, 0, 0, w, h, Math.max(3, w * 0.075)); c.fillStyle = '#fbfaf6'; c.fill(); c.restore();
    }
    if (code) drawFace(c, code, w, h); else drawBack(c, w, h, this.th);
    this.sprites[key] = cv; this.spriteN++;
    return cv;
  };

  // draw a card with its top-left at (x, y); o: {rot, flip (0 back .. 1 face; null = by code), alpha, glow, lift, sx}
  P._card = function (c, code, x, y, w, h, o) {
    o = o || {};
    var k = this.k, flip = o.flip == null ? (code ? 1 : 0) : o.flip, face = flip >= 0.5 && code, sx = Math.abs(Math.cos(flip * Math.PI));
    if (o.flip == null) sx = 1;
    if (sx < 0.02) return;
    var plain = !!(o.glow || o.noShadow), m = plain ? 0 : CARD_M, spr = this._sprite(face ? code : null, w, h, plain);
    c.save();
    c.translate(x + w / 2, y + h / 2 - (o.lift || 0));
    if (o.rot) c.rotate(o.rot);
    c.scale(sx * (o.scale || 1), (o.scale || 1));
    if (o.alpha != null && o.alpha < 1) c.globalAlpha = clamp(o.alpha, 0, 1);
    if (o.glow) { c.shadowColor = o.glow; c.shadowBlur = (o.glowBlur || 22) * k; }
    c.drawImage(spr, 0, 0, spr.width, spr.height, -w / 2 - m, -h / 2 - m, w + 2 * m + 2 / k, h + 2 * m + 2 / k);
    c.restore();
    this.drawn.push({ code: face ? code : null, x: x, y: y, w: w, h: h, rot: o.rot || 0 });
  };

  // ---- chip piles are cached as sprites (a pile is a dozen gradients)
  P._pile = function (amount, r) {
    var key = 'p|' + amount + '|' + r.toFixed(1) + '|' + this.k.toFixed(2) + '|' + this.cfg.theme, s = this.sprites[key];
    if (s) return s;
    var k = this.k, W = Math.ceil(6.6 * r + 18), H = Math.ceil(6.2 * r + 18);
    var cv = mk(W * k, H * k), c = cv.getContext('2d');
    c.setTransform(k, 0, 0, k, 0, 0);
    var ax = W / 2, ay = H - 9 - r * 0.6;
    drawChipPile(c, ax, ay, r, amount);
    s = { cv: cv, w: W, h: H, ax: ax, ay: ay };
    this.sprites[key] = s; this.spriteN++;
    return s;
  };
  P._drawPile = function (c, x, y, r, amount, o) {
    if (!(amount > 0)) return;
    o = o || {};
    var s = this._pile(Math.round(amount), r);
    c.save();
    if (o.alpha != null) c.globalAlpha = clamp(o.alpha, 0, 1);
    if (o.scale && o.scale !== 1) { c.translate(x, y); c.scale(o.scale, o.scale); c.translate(-x, -y); }
    c.drawImage(s.cv, x - s.ax, y - s.ay - (o.lift || 0), s.w, s.h);
    c.restore();
  };

  // ---- Hex
  P._drawAvatar = function (c, g, el, t) {
    var th = this.th, st = { smile: 0.62 }, bp = t % 4300;
    st.blink = bp < 150 ? Math.sin(bp / 150 * Math.PI) : 0;
    var speaking = g && this.cfg.show_captions && g.say && now() - this.sayAt < 3400;
    st.mouth = speaking ? 0.35 + 0.6 * Math.abs(Math.sin(t / 95)) * (0.6 + 0.4 * Math.sin(t / 310)) : 0;
    st.look = Math.sin(t / 2300) * 0.3;
    if (g && g.deal && (g.phase === 'dealing' || g.phase === 'resolve')) {
      var kk = Math.floor((el - g.deal.t0) / Math.max(60, g.deal.step));
      var o = g.deal.order && g.deal.order[clamp(kk, 0, g.deal.order.length - 1)];
      if (o && o[0] > 0 && this.lay.seats[o[0] - 1]) st.look = clamp((this.lay.seats[o[0] - 1].x - 960) / 640, -1, 1);
    }
    if (g && g.phase === 'dealer' && g.dealer && g.dealer.bust && el > 1400) { st.brow = 1; st.mouth = 0.8; st.smile = 0.1; }
    else if (g && g.phase === 'settle' && g.last) { st.smile = g.last.net < 0 ? 0.2 : 1; st.brow = g.last.net < 0 ? 0.8 : 0; }
    // a soft light behind him
    c.save();
    var lg = c.createRadialGradient(960, 120, 20, 960, 130, 190);
    lg.addColorStop(0, rgba(th.accent.charAt(0) === '#' ? th.accent : '#f0c75e', 0.28)); lg.addColorStop(1, 'rgba(0,0,0,0)');
    c.fillStyle = lg; c.fillRect(740, 0, 440, 260);
    c.restore();
    drawDealer(c, DEALER.x, DEALER.headY + Math.sin(t / 900) * 1.6, 1.12, st, th);
  };

  P._banner = function (c, g, el, t) {
    var th = this.th, cx = 960, y = DEALER.bannerY, text, sub = '', left = this._left(t), frac = null, secs = null;
    if (g) {
      text = PHASE_TEXT[g.phase] || '';
      if (g.phase === 'betting') text = g.hand_no ? 'NEXT HAND · ANTE UP' : 'PLACE YOUR BETS';
      else if (g.phase === 'dealing') text = g.deal && g.deal.shuffle && el < g.deal.shuffle ? 'SHUFFLING' : 'DEALING';
      else if (g.phase === 'action') text = g.round > 1 ? 'ROUND ' + g.round + ' · HIT OR STAND?' : 'HIT OR STAND?';
      else if (g.phase === 'insurance') text = 'INSURANCE?  PAYS 2 TO 1';
      if (g.closing && g.phase !== 'over') sub = 'LAST HAND';
      if (left != null) { secs = Math.ceil(left / 1000); frac = clamp(left / Math.max(1, +g.phase_ms || left), 0, 1); }
      if (g.phase === 'dealing' || g.phase === 'resolve' || g.phase === 'dealer' || g.phase === 'settle') { if (g.phase !== 'settle') secs = null; }
    } else {
      text = 'TABLE OPENS SOON';
    }
    var urgent = secs != null && secs <= 3 && g && (g.phase === 'betting' || g.phase === 'action' || g.phase === 'insurance');
    var pulse = urgent ? 0.5 + 0.5 * Math.sin(t / 110) : 0;
    c.save();
    c.font = '800 23px ' + SANS;
    var tw = c.measureText(text).width, w = clamp(tw + (secs != null ? 150 : 70), 330, 450), h = 50;
    rrect(c, cx - w / 2, y - h / 2, w, h, 25);
    var bg = c.createLinearGradient(0, y - h / 2, 0, y + h / 2);
    bg.addColorStop(0, 'rgba(14,20,26,.92)'); bg.addColorStop(1, 'rgba(6,9,13,.92)');
    c.shadowColor = 'rgba(0,0,0,.5)'; c.shadowBlur = 16; c.shadowOffsetY = 5;
    c.fillStyle = bg; c.fill(); c.shadowBlur = 0; c.shadowOffsetY = 0;
    c.lineWidth = 2.2; c.strokeStyle = urgent ? 'rgba(255,' + Math.round(110 + 80 * pulse) + ',80,.95)' : th.trim; c.stroke();
    // progress
    if (frac != null) {
      c.save(); rrect(c, cx - w / 2, y - h / 2, w, h, 25); c.clip();
      c.fillStyle = urgent ? 'rgba(255,100,70,.85)' : rgba(th.trim.charAt(0) === '#' ? th.trim : '#e6c06c', 0.9);
      c.fillRect(cx - w / 2, y + h / 2 - 5, w * frac, 5);
      c.restore();
    }
    c.fillStyle = th.ink; c.textBaseline = 'middle'; c.textAlign = secs != null ? 'left' : 'center';
    c.fillText(text, secs != null ? cx - w / 2 + 26 : cx, y - (sub ? 7 : 1));
    if (sub) { c.font = '800 13px ' + SANS; c.fillStyle = th.accent; c.fillText(sub, secs != null ? cx - w / 2 + 26 : cx, y + 13); }
    if (secs != null) {
      c.font = '800 28px ' + SANS; c.textAlign = 'right'; c.fillStyle = urgent ? '#ff8a6a' : th.accent;
      c.fillText(String(secs), cx + w / 2 - 24, y - 1);
      if (urgent && g) this._event('tick|' + g.id + '|' + g.hand_no + '|' + g.phase + '|' + g.round + '|' + secs, 0, 1, 'tick');
    }
    c.restore();
  };

  // the dealer's table talk
  P._bubble = function (c, g, t) {
    if (!this.cfg.show_captions || !g || !g.say) return;
    var age = now() - this.sayAt, dur = 4600;
    if (age > dur) return;
    var a = clamp(Math.min(age / 250, (dur - age) / 400), 0, 1), th = this.th;
    var text = (this.cfg.dealer_name || 'Hex') + ' says: ' + g.say.text;
    c.save(); c.globalAlpha = a;
    c.font = 'italic 700 21px ' + SERIF;
    var maxW = 330, words = text.split(' '), lines = [], cur = '';
    for (var i = 0; i < words.length; i++) {
      var tryL = cur ? cur + ' ' + words[i] : words[i];
      if (c.measureText(tryL).width > maxW && cur) { lines.push(cur); cur = words[i]; } else cur = tryL;
    }
    if (cur) lines.push(cur);
    lines = lines.slice(0, 3);
    var w = 0; for (i = 0; i < lines.length; i++) w = Math.max(w, c.measureText(lines[i]).width);
    var h = lines.length * 27 + 22, bw = w + 34, x1 = 790, x0 = x1 - bw, y0 = 62 - (lines.length - 1) * 4 + 8 * (1 - a);
    c.shadowColor = 'rgba(0,0,0,.4)'; c.shadowBlur = 14; c.shadowOffsetY = 5;
    rrect(c, x0, y0, bw, h, 16); c.fillStyle = 'rgba(255,252,242,.97)'; c.fill();
    c.beginPath(); c.moveTo(x1 - 2, y0 + h * 0.5 - 10); c.lineTo(x1 + 30, y0 + h * 0.5 + 16); c.lineTo(x1 - 2, y0 + h * 0.5 + 14); c.closePath(); c.fill();
    c.shadowBlur = 0; c.shadowOffsetY = 0;
    c.fillStyle = '#2a2118'; c.textAlign = 'left'; c.textBaseline = 'middle';
    for (i = 0; i < lines.length; i++) c.fillText(lines[i], x0 + 17, y0 + 11 + 13.5 + i * 27);
    c.restore();
  };

  // the waiting list (left) and the table's info (right)
  P._boards = function (c, g, el, t) {
    if (!this.cfg.show_players) return;
    var th = this.th, i, x0 = 130, y0 = 284, w = 322;
    var seated = 0, free = 0, queue = [];
    if (g) { for (i = 0; i < g.seats.length; i++) { if (g.seats[i] && g.seats[i].user) seated++; else free++; } queue = g.queue || []; }
    else if (this.idle) free = +this.idle.seats || 0;
    c.save();
    c.font = '800 15px ' + SANS;
    var names = [], max = clamp(+this.cfg.players_max || 8, 1, 20);
    for (i = 0; i < Math.min(queue.length, max); i++) names.push(queue[i].user);
    // wrap the names into at most 2 lines
    var lines = [], cur = '', shown = 0;
    c.font = '700 15px ' + SANS;
    for (i = 0; i < names.length; i++) {
      var piece = (i + 1) + '. ' + names[i], tryL = cur ? cur + '   ' + piece : piece;
      if (c.measureText(tryL).width > w - 24 && cur) { if (lines.length >= 1) break; lines.push(cur); cur = piece; } else cur = tryL;
      shown = i + 1;
    }
    if (cur) lines.push(cur);
    var more = queue.length - shown;
    var h = 40 + (lines.length ? lines.length * 21 + (more > 0 ? 0 : 0) : 0) + (more > 0 ? 0 : 0);
    rrect(c, x0, y0, w, h, 12); c.fillStyle = 'rgba(6,10,14,.62)'; c.fill(); c.lineWidth = 1.6; c.strokeStyle = th.trimDim; c.stroke();
    c.font = '800 14px ' + SANS; c.textBaseline = 'middle'; c.textAlign = 'left';
    c.fillStyle = th.accent; c.fillText('WAITING', x0 + 14, y0 + 20);
    c.fillStyle = th.ink; c.fillText(String(queue.length), x0 + 14 + c.measureText('WAITING ').width, y0 + 20);
    c.textAlign = 'right'; c.fillStyle = th.inkDim; c.fillText(g ? seated + '/' + (seated + free) + ' SEATED' : free + ' SEATS', x0 + w - 14, y0 + 20);
    c.textAlign = 'left'; c.font = '700 15px ' + SANS; c.fillStyle = th.ink;
    for (i = 0; i < lines.length; i++) c.fillText(lines[i] + (i === lines.length - 1 && more > 0 ? '  +' + more : ''), x0 + 14, y0 + 44 + i * 21);
    // right: the table's limits and commands
    var rx = 1920 - 130 - w, cur2 = (g && g.currency) || (this.idle && this.idle.currency) || this.cfg.currency || 'hexcoins';
    var mn = g ? g.min_bet : (this.idle && this.idle.min_bet) || this.cfg.min_bet, mx = g ? g.max_bet : (this.idle && this.idle.max_bet) || this.cfg.max_bet;
    var cmd = (g && g.commands_text) || (this.idle && this.idle.commands_text) || this.cfg.commands_text || '';
    var rh = cmd ? 62 : 40;
    rrect(c, rx, y0, w, rh, 12); c.fillStyle = 'rgba(6,10,14,.62)'; c.fill(); c.lineWidth = 1.6; c.strokeStyle = th.trimDim; c.stroke();
    c.font = '800 14px ' + SANS; c.textAlign = 'left'; c.fillStyle = th.accent;
    c.fillText(g ? 'HAND #' + Math.max(1, g.hand_no) : 'BLACKJACK', rx + 14, y0 + 20);
    c.textAlign = 'right'; c.fillStyle = th.inkDim;
    c.fillText('BETS ' + fmtShort(mn) + (mx ? ' – ' + fmtShort(mx) : '+') + ' ' + String(cur2).toUpperCase(), rx + w - 14, y0 + 20);
    if (cmd) {
      c.textAlign = 'left'; c.fillStyle = th.ink; var ct = fit(c, cmd, '700 %spx ' + SANS, w - 28, 15, 10);
      c.fillText(ct, rx + 14, y0 + 44);
    }
    c.restore();
  };

  // ---- the shoe and the discard tray
  P._shoeAndTray = function (c, g, el, t) {
    if (!this.cfg.show_shoe) return;
    var th = this.th, i, sh = g && g.shoe;
    var info, tray;
    if (sh && sh.size) {
      // cards still on their way out of the shoe in this phase (the STATE already counts them as dealt)
      var pending = 0, d = g.deal;
      if (d && d.order && (g.phase === 'dealing' || g.phase === 'resolve' || g.phase === 'dealer')) {
        for (i = 0; i < d.order.length; i++) if (el < d.t0 + i * d.step) pending++;
      }
      info = { left: sh.left + pending, size: sh.size, to_cut: sh.to_cut + (sh.cut_passed ? 0 : pending), cut_passed: sh.cut_passed, decks: sh.decks };
      tray = { discards: sh.discards, size: sh.size };
      // the shuffle: the discards go back into the shoe
      if (g.phase === 'dealing' && d && d.shuffle > 0 && el < d.shuffle + 300) {
        var p = clamp(el / d.shuffle, 0, 1), was = this.lastShoe || { discards: 0 };
        info.left = Math.round(lerp(Math.max(0, sh.left * 0.1), sh.left + pending, easeOut(p)));
        tray = { discards: Math.round(lerp(was.discards || 0, 0, easeOut(p))), size: sh.size };
        info.cut_passed = false; info.to_cut = Math.round(lerp(0, sh.to_cut + pending, p));
        this._shuffleFx(c, el, d.shuffle);
        this._event('shuf|' + g.id + '|' + g.hand_no, 0, el, null) && this._snd('shuffle');
      }
    } else {
      var decks = 2;
      if (this.idle && this.idle.rules && this.idle.rules.deck_table && this.idle.rules.deck_table[0]) decks = this.idle.rules.deck_table[0].decks;
      if (g && sh && sh.next_decks) decks = sh.next_decks;
      info = { left: decks * 52, size: decks * 52, to_cut: Math.round(decks * 52 * 0.75), cut_passed: false, decks: decks };
      tray = { discards: 0, size: decks * 52 };
    }
    drawShoe(c, SHOE_XY.x, SHOE_XY.y, th, info, Math.sin(t / 220) > 0);
    drawTray(c, TRAY_XY.x, TRAY_XY.y, th, tray);
  };

  // cards riffling from the tray into the shoe
  P._shuffleFx = function (c, el, dur) {
    var step = 85, last = Math.floor(el / step), i;
    for (i = last; i > last - 6; i--) {
      if (i < 0 || i * step > dur) continue;
      var u = (el - i * step) / 520;
      if (u < 0 || u > 1) continue;
      var r = mulberry(i * 31 + 7)(), p = easeOut(u);
      var x = lerp(TRAY_XY.x + 60, SHOE_MOUTH.x - 10, p), y = lerp(TRAY_XY.y + 8, SHOE_MOUTH.y + 8, p) - Math.sin(Math.PI * p) * (12 + r * 20);
      this._card(c, null, x - 22, y - 30, 44, 62, { rot: (r - 0.5) * 1.2 + p * 0.4, alpha: 1 - Math.pow(u, 6), noShadow: false });
    }
  };

  // ---- seats
  P._plate = function (c, x, y, w, h, name, seatN, mode, t) {
    var th = this.th, edge = th.plateEdge, alpha = 1, glow = null;
    if (mode === 'held') { alpha = 0.62; edge = th.trimDim; }
    else if (mode === 'turn') { glow = th.accent; }
    else if (mode === 'win') { glow = C_WIN; edge = C_WIN; }
    c.save(); c.globalAlpha = alpha;
    if (glow) { c.shadowColor = glow; c.shadowBlur = 16; }
    rrect(c, x - w / 2, y - h / 2, w, h, h * 0.36);
    var g = c.createLinearGradient(0, y - h / 2, 0, y + h / 2); g.addColorStop(0, th.plate1); g.addColorStop(1, th.plate2);
    c.fillStyle = g; c.fill(); c.shadowBlur = 0;
    c.lineWidth = 2; c.strokeStyle = edge; c.stroke();
    // seat number tag
    var r = h * 0.34, tx = x - w / 2 + h * 0.5;
    c.beginPath(); c.arc(tx, y, r, 0, TAU); c.fillStyle = rgba(edge.charAt(0) === '#' ? edge : '#e6c06c', 0.9); c.fill();
    c.fillStyle = 'rgba(15,10,0,.92)'; c.font = '800 ' + Math.round(h * 0.42) + 'px ' + SANS; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText(String(seatN), tx, y + 0.5);
    c.fillStyle = th.plateInk; c.textAlign = 'left';
    var label = fit(c, name, '800 %spx ' + SANS, w - h * 1.2 - 8, Math.round(h * 0.56), 11);
    c.fillText(label, x - w / 2 + h * 0.98, y + 1);
    c.restore();
  };

  // where the stakes of a seat's hands stand: [{x, y, r}] for n hands
  function stakeSpots(nh, lay, A) {
    var r = lay.cr, y = A.y + lay.R * 0.52, out = [], i;
    if (nh <= 1) return [{ x: A.x, y: y, r: r }];
    if (nh === 2) { var dx = lay.R * 0.78; return [{ x: A.x - dx, y: y, r: r * 0.78 }, { x: A.x + dx, y: y, r: r * 0.78 }]; }
    for (i = 0; i < nh; i++) out.push({ x: A.x + (i - (nh - 1) / 2) * lay.R * 0.62, y: y + (i % 2 ? 4 : -2), r: r * 0.6 });
    return out;
  }

  // the stacks of cards of one seat's hands: [{cx, by, sc, cap}] (by = the bottom of the lowest card of the hand,
  // cap = how tall a stack may be). Three or four hands (splits) stand in two rows.
  var ROW_GAP = 30;
  function handBoxes(nh, heights, lay, A, sc, cap) {
    var bottom = A.y - lay.R - 13, out = [], i;
    if (nh <= 1) return [{ cx: A.x, by: bottom, sc: sc, cap: cap }];
    var colW = lay.cw * sc, dx = colW / 2 + 3;
    if (nh === 2) return [{ cx: A.x - dx, by: bottom, sc: sc, cap: cap }, { cx: A.x + dx, by: bottom, sc: sc, cap: cap }];
    var h0 = Math.max(heights[0] || 0, heights[1] || 0) || lay.ch * sc;
    for (i = 0; i < nh; i++) out.push({ cx: A.x + (i % 2 ? dx : -dx), by: i < 2 ? bottom : bottom - h0 - ROW_GAP, sc: sc, cap: cap });
    return out;
  }
  // The most a hand's stack of cards may grow: a long hand overlaps more (down to a corner index showing) instead of
  // reaching the dealer, the panels, the shoe or the tray; `cap` is the room this hand has.
  var STACK_MAX = 340;
  function stackDy(n, ch, cap) {
    if (n <= 1) return 0;
    var c = Math.max(ch * 1.5, Math.min(2.6 * ch, STACK_MAX));
    if (cap) c = Math.min(c, cap);
    return clamp((c - ch) / (n - 1), 0.2 * ch, 0.36 * ch);
  }
  function stackHeight(n, ch, cap) { return n <= 1 ? ch : ch + (n - 1) * stackDy(n, ch, cap); }
  // keep-out areas on the felt [x0, x1, bottom y]: the waiting list, the limits panel, the discard tray, the shoe and the dealer's cards
  var KEEP = [[128, 456, 350], [1464, 1794, 348], [296, 520, 458], [1384, 1690, 472], [840, 1150, 500]];   // (the last: the dealer's cards)
  function keepBottom(x0, x1) {
    var y = 0, i, k;
    for (i = 0; i < KEEP.length; i++) { k = KEEP[i]; if (Math.min(x1, k[1]) - Math.max(x0, k[0]) > 12 && k[2] > y) y = k[2]; }
    return y;
  }

  // ---- one stake drops onto the felt
  P._chipDrop = function (key, amt, t) {
    var rec = this.chipAt[key];
    var quiet = !this.born || t - this.born < 700;
    if (!rec) { rec = this.chipAt[key] = { amt: amt, at: quiet ? -1e9 : t }; if (!quiet) this._snd('chip'); }
    else if (amt > rec.amt) { rec.at = t; this._snd('chip'); }
    rec.amt = amt;
    var u = clamp((t - rec.at) / 340, 0, 1), e = easeOut(u);
    return { scale: lerp(1.38, 1, e), lift: lerp(30, 0, e), alpha: clamp(u * 4, 0, 1) };
  };

  // the net of a seat after settling (from the STATE's last-hand table)
  function seatNet(g, n) {
    var rs = g.last && g.last.results;
    if (!rs) return null;
    for (var i = 0; i < rs.length; i++) if (rs[i].seat === n) return rs[i];
    return null;
  }

  // ---- the stakes of one seat
  P._stakes = function (c, seat, A, g, el, t) {
    var lay = this.lay, th = this.th, phase = g.phase, hands = seat.hands || [], nh = hands.length, r = lay.cr, i;
    var labelY = A.y + lay.R + 3, showLabel = null;
    if (!nh || phase === 'betting') {
      if (seat.bet > 0) {
        var dr = this._chipDrop('s' + seat.n, seat.bet, t);
        this._drawPile(c, A.x, A.y + lay.R * 0.52, r, seat.bet, { alpha: dr.alpha, scale: dr.scale, lift: dr.lift });
        showLabel = { text: fmtShort(seat.bet), col: th.accent };
      } else if (seat.state === 'held' && phase !== 'over') {          // (a closed table holds nothing)
        if (seat.last_bet > 0) this._drawPile(c, A.x, A.y + lay.R * 0.52, r, seat.last_bet, { alpha: 0.26 });
        var pulse = 0.5 + 0.5 * Math.sin(t / 260);
        showLabel = { text: seat.last_bet > 0 ? 'ANTE UP · ' + fmtShort(seat.last_bet) : 'ANTE UP', col: '#ffd76a', glow: 'rgba(255,215,106,' + (0.25 + 0.5 * pulse).toFixed(2) + ')', border: 'rgba(255,215,106,' + (0.35 + 0.5 * pulse).toFixed(2) + ')' };
      }
    } else {
      var spots = stakeSpots(nh, lay, A), settle = phase === 'settle';
      for (i = 0; i < nh; i++) {
        var h = hands[i], sp = spots[i], res = settle ? h.result : null, amt = h.bet, extra = 0;
        if (nh === 1) {
          if (h.doubled) { amt = h.bet / 2; extra = h.bet / 2; }
          else if (h.pending > 0) { extra = h.pending; }
        } else amt = h.bet + (h.pending || 0);
        var x1 = sp.x, x2 = sp.x;
        if (extra) { x1 = sp.x - sp.r * 1.0; x2 = sp.x + sp.r * 1.0; }
        var a1 = 1, a2 = 1, dx1 = 0, dy1 = 0, pay = null;
        if (res) {
          var u1 = easeOut(clamp((el - 450) / 900, 0, 1)), u2 = ease(clamp((el - 1750) / 800, 0, 1));
          var win = res.outcome === 'win' || res.outcome === 'blackjack' || res.outcome === 'charlie';
          var lose = res.outcome === 'lose' || res.outcome === 'bust';
          var plate = { x: A.x, y: A.y + lay.R + 28 };
          if (lose) {
            var p = ease(clamp((el - 600) / 900, 0, 1));
            dx1 = (960 - sp.x) * p; dy1 = (DEALER.bannerY + 30 - sp.y) * p; a1 = 1 - Math.pow(p, 3);
            a2 = a1;
          } else {
            if (win && res.net > 0) pay = { amt: res.net, x: lerp(960, sp.x + sp.r * (extra ? 2.2 : 1.5), u1), y: lerp(DEALER.bannerY + 30, sp.y - 2, u1) };
            dx1 = (plate.x - sp.x) * u2; dy1 = (plate.y - sp.y) * u2; a1 = 1 - u2 * 0.95; a2 = a1;
            if (pay) { pay.x += (plate.x - pay.x) * u2; pay.y += (plate.y - pay.y) * u2; pay.a = a1; }
          }
          this._event('chips|' + g.id + '|' + g.hand_no + '|' + i + '|' + seat.n, lose ? 600 : 450, el, win ? 'chips' : null);
        }
        if (a1 > 0.01) {
          this._drawPile(c, x1 + dx1, sp.y + dy1, sp.r, amt, { alpha: a1 });
          if (extra) this._drawPile(c, x2 + dx1, sp.y + dy1 - 2, sp.r, extra, { alpha: a2 });
          if (extra && !res && h.doubled) { c.save(); c.font = '800 ' + Math.round(sp.r * 0.66) + 'px ' + SANS; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillStyle = '#ffd76a'; c.strokeStyle = 'rgba(0,0,0,.7)'; c.lineWidth = 3; c.strokeText('×2', x2 + sp.r * 1.05, sp.y - sp.r * 0.15); c.fillText('×2', x2 + sp.r * 1.05, sp.y - sp.r * 0.15); c.restore(); }
        }
        if (pay && (pay.a == null || pay.a > 0.01)) this._drawPile(c, pay.x, pay.y, sp.r, pay.amt, { alpha: pay.a == null ? 1 : pay.a });
      }
      if (seat.ins > 0) {
        var ir = r * 0.62, ix = A.x - lay.R - ir * 1.2, iy = A.y + lay.R * 0.5;
        var ia = 1;
        if (phase === 'settle') {
          var ig = ease(clamp((el - 600) / 900, 0, 1)), won = g.dealer && g.dealer.bj; ia = won ? 1 - ease(clamp((el - 1750) / 800, 0, 1)) : 1 - ig;
          if (!won) { ix += (960 - ix) * ig; iy += (DEALER.bannerY - iy) * ig; }
        }
        if (ia > 0.01) {
          this._drawPile(c, ix, iy, ir, seat.ins, { alpha: ia });
          c.save(); c.globalAlpha = ia; c.font = '800 ' + Math.max(10, Math.round(ir * 0.7)) + 'px ' + SANS; c.textAlign = 'center'; c.fillStyle = '#9fe6b5';
          c.fillText('INS', ix, iy + ir * 0.95); c.restore();
        }
      }
      if (phase === 'settle') {
        var sn = seatNet(g, seat.n);
        if (sn && el > 700) showLabel = { text: sn.net === 0 ? 'EVEN' : (sn.net > 0 ? '+' : '−') + fmtShort(Math.abs(sn.net)), col: sn.net > 0 ? C_WIN : sn.net < 0 ? C_LOSE : C_PUSH, bold: true };
      } else if (seat.stake > 0) showLabel = { text: fmtShort(seat.stake), col: th.accent };
    }
    if (showLabel) {
      c.save(); if (showLabel.alpha != null) c.globalAlpha = showLabel.alpha;
      var lh = clamp(Math.round(lay.S * 0.15), 20, 28);
      pill(c, A.x, labelY, showLabel.text, { h: lh, fg: showLabel.col, bg: 'rgba(5,8,12,.88)', border: showLabel.border || 'rgba(255,255,255,.18)', glow: showLabel.glow, bw: 1.4, maxW: lay.S - 6, minW: 40 });
      c.restore();
    }
  };

  // ---- the cards of one hand (and its badges); returns the top of the stack
  P._hand = function (c, seat, hand, hi, box, g, el, t) {
    var lay = this.lay, th = this.th, d = g.deal, codes = hand.cards, nc = codes.length, j;
    var cw = lay.cw * box.sc, ch = lay.ch * box.sc, started = 0, landedAll = true, landed = [], lastStart = -1;
    var starts = [];
    for (j = 0; j < nc; j++) {
      var sk = this.sched[hand.uid + ':' + j], st = (d && sk != null) ? d.t0 + sk * d.step : -1e9;
      starts.push(st);
      if (el >= st) started++; else landedAll = false;
      if (el >= st + FLY) landed.push(codes[j]); else landedAll = false;
      if (sk != null) lastStart = Math.max(lastStart, st);
    }
    if (!started) return null;
    var dy = stackDy(started, ch, box.cap);
    // a hand that busts shakes when the busting card lands
    var shake = 0;
    if (hand.status === 'bust' && nc > 1) {
      var since = el - (starts[nc - 1] + FLY);
      if (since >= 0 && since < 650) { shake = Math.sin(since / 42) * 8 * (1 - since / 650); this._event('bust|' + hand.uid + '|' + nc, starts[nc - 1] + FLY, el, 'bust'); }
    }
    var result = g.phase === 'settle' ? hand.result : null;
    var win = result && (result.outcome === 'win' || result.outcome === 'blackjack' || result.outcome === 'charlie');
    var dim = result && (result.outcome === 'lose' || result.outcome === 'bust') ? clamp((el - 800) / 600, 0, 1) * 0.42 : 0;
    var topY = box.by - ch, a = 1 - Math.exp(-Math.min(100, t - this.lastT0) / 85), seen = {};
    for (j = 0; j < started; j++) {
      var key = hand.uid + ':' + j, tx = box.cx - cw / 2 + (j - (started - 1) / 2) * cw * 0.07 + shake, ty = box.by - ch - (started - 1 - j) * dy;
      if (j === 0) topY = ty;
      var u = (el - starts[j]) / FLY, pp = this.cardPos[key];
      var code = codes[j];
      if (u < 1 && starts[j] > -1e8) {
        // in flight: out of the shoe, over the felt, flipping as it comes down
        var p = easeOut(clamp(u, 0, 1)), sx = SHOE_MOUTH.x, sy = SHOE_MOUTH.y;
        var fx = lerp(sx, tx, p), fy = lerp(sy, ty, p) - Math.sin(Math.PI * p) * 70;
        this.cardPos[key] = { x: tx, y: ty };
        this._queue(code, fx, fy, cw, ch, { rot: lerp(-0.45, 0, p), flip: code ? clamp((u - 0.45) / 0.4, 0, 1) : 0, alpha: 1, scale: lerp(0.9, 1, p) });
        this._event('card|' + hand.uid + '|' + j, starts[j], el, 'card');
        continue;
      }
      if (!pp) pp = this.cardPos[key] = this.entryFrom[key] ? { x: this.entryFrom[key].x, y: this.entryFrom[key].y } : { x: tx, y: ty };
      pp.x += (tx - pp.x) * a; pp.y += (ty - pp.y) * a;
      var glow = null;
      if (win) glow = (result.outcome === 'blackjack' ? C_BJ : C_WIN);
      this._card(c, code, pp.x, pp.y, cw, ch, { alpha: 1 - dim, glow: glow ? rgba(glow, 0.55 + 0.35 * Math.sin(t / 240)) : null, glowBlur: 20 });
    }
    // the badges on top of the stack
    var vis = handValue(landed), pillY = topY - 17 * Math.max(0.8, box.sc);
    var ph = clamp(Math.round(lay.S * 0.17), 22, 30) * (box.sc < 1 ? 0.88 : 1);
    var tw = null, text = null, o = {};
    if (landed.length >= 2 || (landed.length === nc && nc >= 2)) {
      text = String(vis.total);
      o = { h: ph, fg: '#fff', border: 'rgba(255,255,255,.35)' };
      if (vis.soft && vis.total < 21) text = 'SOFT ' + vis.total;
      if (hand.status === 'blackjack' && landed.length >= 2) { text = 'BLACKJACK'; o = { h: ph, fg: '#2a1c00', bg: C_BJ, border: '#fff3b0', glow: 'rgba(255,213,74,.8)' }; }
      else if (landed.length === nc && vis.total > 21) { text = 'BUST ' + vis.total; o = { h: ph, fg: '#fff', bg: '#c42a22', border: '#ff9a90', glow: 'rgba(255,80,70,.7)' }; }
      else if (hand.status === 'charlie' && landed.length === nc) { text = 'CHARLIE'; o = { h: ph, fg: '#2a1c00', bg: C_WIN, border: '#d7ffe6' }; }
      else if (hand.status === 'surrender') { text = 'SURRENDER'; o = { h: ph, fg: '#e9edf6', bg: '#4b5568', border: '#aab3c4' }; }
      else if (landed.length === nc && vis.total === 21) { o.fg = '#ffd76a'; o.border = '#ffd76a'; }
      o.maxW = Math.max(46, lay.S - 10);
      o.minW = 32;
    }
    var act = (g.phase === 'action' || g.phase === 'resolve') && hand.act && hand.status === 'active' ? hand.act : null;
    if (text) {
      var actTxt = act && g.phase === 'action' ? STATUS_WORD[act] : null, w1, w2 = 0;
      c.save(); c.font = '800 ' + Math.round(o.h * 0.62) + 'px ' + SANS;
      w1 = c.measureText(text).width + 20; if (actTxt) { c.font = '800 ' + Math.round(o.h * 0.52) + 'px ' + SANS; w2 = c.measureText(actTxt).width + 16; }
      c.restore();
      var tot = w1 + (actTxt ? w2 + 4 : 0), xcx = box.cx + shake;
      pill(c, actTxt ? xcx - tot / 2 + w1 / 2 : xcx, pillY, text, o);
      if (actTxt) pill(c, xcx + tot / 2 - w2 / 2, pillY, actTxt, { h: o.h * 0.88, fg: '#0c1118', bg: STATUS_COL[act], font: '800 ' + Math.round(o.h * 0.52) + 'px ' + SANS, pad: 8 });
    }
    // the verdict
    if (result && el > 380) {
      var pop = easeBack(clamp((el - 380) / 380, 0, 1)), oc = result.outcome, col, bg, word;
      if (oc === 'blackjack') { word = 'BLACKJACK'; col = '#2a1c00'; bg = C_BJ; }
      else if (oc === 'win') { word = 'WIN'; col = '#06240f'; bg = C_WIN; }
      else if (oc === 'charlie') { word = 'CHARLIE'; col = '#06240f'; bg = C_WIN; }
      else if (oc === 'push') { word = 'PUSH'; col = '#1c2230'; bg = C_PUSH; }
      else if (oc === 'surrender') { word = 'SURRENDER'; col = '#fff'; bg = '#64708a'; }
      else if (oc === 'bust') { word = 'BUST'; col = '#fff'; bg = '#c42a22'; }
      else { word = 'LOSE'; col = '#fff'; bg = '#b6403a'; }
      var amt = (result.net > 0 ? '+' : result.net < 0 ? '−' : '') + (result.net ? fmtShort(Math.abs(result.net)) : '');
      var many = seat.hands.length > 1, vh = clamp(Math.round(lay.S * 0.22), 28, 40) * (many ? 0.82 : box.sc < 1 ? 0.9 : 1);
      var vy = (topY + box.by) / 2 + (many ? (hi % 2 ? 1 : -1) * Math.min(ch * 0.22, 34) : 0);
      var wtxt = many ? (oc === 'push' ? 'PUSH' : oc === 'bust' ? 'BUST' : amt || word) : word + (amt ? '  ' + amt : '');
      c.save(); c.translate(box.cx, vy); c.scale(pop, pop); c.translate(-box.cx, -vy);
      pill(c, box.cx, vy, wtxt, { h: vh, fg: col, bg: bg, border: 'rgba(255,255,255,.75)', bw: 2, maxW: many ? Math.max(40, lay.cw * box.sc + 16) : Math.max(60, lay.S + 10), glow: win ? 'rgba(255,255,255,.55)' : null, minW: many ? 30 : 50 });
      c.restore();
      if (win) this._sparkles(c, box.cx, vy, el - 400, hand.uid, oc === 'blackjack' ? C_BJ : '#bfffd8');
    }
    return topY;
  };

  P._queue = function (code, x, y, w, h, o) { this.fly.push([code, x, y, w, h, o]); };

  // a burst of sparkles: deterministic in (uid, time since the verdict)
  P._sparkles = function (c, x, y, ms, seed, col) {
    if (ms < 0 || ms > 1100) return;
    var rnd = mulberry(seed * 977 + 13), n = 14, u = ms / 1100;
    c.save();
    for (var i = 0; i < n; i++) {
      var ang = rnd() * TAU, sp = 60 + rnd() * 130, life = 0.55 + rnd() * 0.45;
      if (u > life) continue;
      var px = x + Math.cos(ang) * sp * u, py = y + Math.sin(ang) * sp * u + 120 * u * u, r = 2 + rnd() * 3.2, al = 1 - u / life;
      c.globalAlpha = al; c.fillStyle = col;
      c.beginPath(); c.moveTo(px, py - r * 1.6); c.lineTo(px + r * 0.5, py - r * 0.5); c.lineTo(px + r * 1.6, py); c.lineTo(px + r * 0.5, py + r * 0.5);
      c.lineTo(px, py + r * 1.6); c.lineTo(px - r * 0.5, py + r * 0.5); c.lineTo(px - r * 1.6, py); c.lineTo(px - r * 0.5, py - r * 0.5); c.closePath(); c.fill();
    }
    c.restore();
  };

  // ---- one seat
  P._seat = function (c, i, seat, g, el, t) {
    var lay = this.lay, th = this.th, A = lay.seats[i], phase = g.phase;
    if (!seat || !seat.user) {
      c.save(); c.globalAlpha = phase === 'betting' ? 0.5 : 0.24; c.fillStyle = th.ink; c.font = '800 ' + Math.round(lay.R * 0.62) + 'px ' + SANS;
      c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(String(i + 1), A.x, A.y + 1); c.restore();
      return;
    }
    var hands = seat.hands || [], nh = hands.length, j, mode = seat.state === 'held' ? 'held' : '';
    this._stakes(c, seat, A, g, el, t);
    if (nh) {
      // sizes first (the rows of a split need each hand's height); the cards shrink a little rather than reach
      // the panels, the tray or the shoe
      var sc = nh === 2 ? Math.min(0.8, (lay.S - 10) / (2 * lay.cw + 6)) : nh > 2 ? Math.min(0.68, (lay.S - 10) / (2 * lay.cw + 6)) : 1, heights = [], cap = 0, pass;
      if (!isFinite(sc) || sc <= 0) sc = 0.7;
      var bottom = A.y - lay.R - 13, counts = [];
      for (j = 0; j < nh; j++) {
        var cnt = 0, hh = hands[j];
        for (var q = 0; q < hh.cards.length; q++) { var sk = this.sched[hh.uid + ':' + q]; if (!(g.deal && sk != null) || el >= g.deal.t0 + sk * g.deal.step) cnt++; }
        counts.push(Math.max(1, cnt));
      }
      for (pass = 0; pass < 4; pass++) {
        var gw = nh === 1 ? lay.cw : 2 * lay.cw * sc + 6, half = Math.max(gw / 2 + 6, 44);
        var room = bottom - (keepBottom(A.x - half, A.x + half) + 36), need;
        cap = nh > 2 ? (room - ROW_GAP) / 2 : room;
        heights = [];
        for (j = 0; j < nh; j++) heights.push(stackHeight(counts[j], lay.ch * sc, cap));
        need = nh > 2 ? Math.max(heights[0] || 0, heights[1] || 0) + ROW_GAP + Math.max(heights[2] || 0, heights[3] || 0) : Math.max.apply(null, heights);
        if (need <= room + 1 || sc <= 0.5) break;
        sc = Math.max(0.5, sc * Math.max(0.8, room / need));
      }
      var boxes = handBoxes(nh, heights, lay, A, sc, cap), wait = false, any = false;
      for (j = 0; j < nh; j++) {
        this._hand(c, seat, hands[j], j, boxes[j], g, el, t);
        if (hands[j].status === 'active' && !hands[j].act) wait = true;
        if (hands[j].result && hands[j].result.net > 0 && phase === 'settle') any = true;
      }
      if (phase === 'action' && wait) mode = 'turn';
      if (phase === 'insurance' && !seat.ins_state && hands[0] && hands[0].bet >= 2) mode = 'turn';
      if (any && el > 400) mode = 'win';
    }
    if (seat.leaving) mode = mode || 'held';
    var pw = lay.plateW, ph = lay.plateH, py = A.y + lay.R + 11 + ph / 2 + 2;
    if (mode === 'turn') { var pulse = 0.5 + 0.5 * Math.sin(t / 200); c.save(); c.globalAlpha = 0.35 + 0.4 * pulse; rrect(c, A.x - pw / 2 - 4, py - ph / 2 - 4, pw + 8, ph + 8, ph * 0.5); c.lineWidth = 3; c.strokeStyle = th.accent; c.stroke(); c.restore(); }
    this._plate(c, A.x, py, pw, ph, seat.leaving ? seat.user + ' · leaving' : seat.user, i + 1, mode, t);
  };

  // ---- the dealer's cards
  P._dealerHand = function (c, g, el, t) {
    var d = g.dealer, deal = g.deal, th = this.th;
    if (!d || !d.cards || !d.cards.length) return;
    var n = d.cards.length, cw = DEALER.cw, ch = DEALER.ch, j, started = 0, landed = [], starts = [];
    for (j = 0; j < n; j++) {
      var sk = this.sched['d:' + j], st = (deal && sk != null) ? deal.t0 + sk * deal.step : -1e9;
      starts.push(st);
      if (el >= st) started++;
      if (el >= st + FLY && d.cards[j]) landed.push(d.cards[j]);
    }
    if (!started) return;
    var dx = started > 5 ? Math.max(34, (560 - cw) / (started - 1)) : DEALER.dx, total = cw + (started - 1) * dx;
    var x0 = DEALER.x - total / 2, y = DEALER.cardY - ch / 2, a = 1 - Math.exp(-Math.min(100, t - this.lastT0) / 85);
    var flipAt = deal && deal.flip != null && g.phase === 'dealer' ? deal.t0 : null, bj = d.bj && !d.hidden;
    for (j = 0; j < started; j++) {
      var key = 'd:' + j, tx = x0 + j * dx, ty = y, code = d.cards[j], u = (el - starts[j]) / FLY, pp = this.cardPos[key];
      var flip = null, lift = 0, rot = 0;
      if (j === 1 && flipAt != null && deal.flip === 1) {
        var fp = clamp((el - flipAt) / 480, 0, 1);
        flip = fp; lift = Math.sin(Math.PI * fp) * 14;
        this._event('flip|' + g.id + '|' + g.hand_no, flipAt + 240, el, 'flip');
      } else if (!code) flip = 0;
      else if (j === 1 && d.hidden === false && g.phase !== 'dealer' && flipAt == null) flip = null;
      if (g.phase === 'action' && g.round === 1 && j === 1 && d.hidden && d.peeked && el < 1300) {      // the peek
        var pk = Math.sin(Math.PI * clamp(el / 1300, 0, 1)); lift = pk * 18; rot = -0.1 * pk;
      }
      if (u < 1 && starts[j] > -1e8) {
        var p = easeOut(clamp(u, 0, 1)), sx = SHOE_MOUTH.x, sy = SHOE_MOUTH.y;
        this.cardPos[key] = { x: tx, y: ty };
        this._queue(code, lerp(sx, tx, p), lerp(sy, ty, p) - Math.sin(Math.PI * p) * 60, cw, ch,
          { rot: lerp(-0.4, 0, p), flip: code ? (j === 0 || starts[j] > 0 && j > 1 ? clamp((u - 0.45) / 0.4, 0, 1) : 0) : 0, scale: lerp(0.9, 1, p) });
        this._event('dcard|' + g.id + '|' + g.hand_no + '|' + j, starts[j], el, 'card');
        continue;
      }
      if (!pp) pp = this.cardPos[key] = { x: tx, y: ty };
      pp.x += (tx - pp.x) * a; pp.y += (ty - pp.y) * a;
      this._card(c, code, pp.x, pp.y, cw, ch, { flip: flip, lift: lift, rot: rot, glow: bj ? rgba(C_BJ, 0.7) : null, glowBlur: 24 });
    }
    // the total
    var shown = handValue(landed);
    if (landed.length) {
      var txt = String(shown.total), o = { h: 34, fg: '#fff', border: 'rgba(255,255,255,.4)' };
      if (shown.soft && shown.total < 21 && !d.hidden) txt = 'SOFT ' + shown.total;
      if (d.hidden) txt += ' + ?';
      if (!d.hidden && d.bj) { txt = 'BLACKJACK'; o = { h: 34, fg: '#2a1c00', bg: C_BJ, border: '#fff3b0', glow: 'rgba(255,213,74,.8)' }; }
      else if (!d.hidden && shown.total > 21 && landed.length === d.cards.length) { txt = 'BUST ' + shown.total; o = { h: 34, fg: '#fff', bg: '#c42a22', border: '#ff9a90', glow: 'rgba(255,80,70,.7)' }; }
      pill(c, x0 + total + 60 + (txt.length > 6 ? 40 : 0), DEALER.cardY, txt, o);
    }
  };

  // ---- the table-closed card
  P._summary = function (c, g, el, t) {
    var s = g.summary; if (!s) return;
    var th = this.th, a = clamp(el / 400, 0, 1), cur = String(s.currency || g.currency || 'hexcoins');
    var x = 960, y = 650, w = 700, all = s.players || [], rows = all.slice(0, 6), more = all.length - rows.length, h = 150 + Math.max(1, rows.length + (more > 0 ? 0.75 : 0)) * 36 + 24;
    c.save(); c.globalAlpha = a * 0.55; c.fillStyle = '#000'; tablePath(c, T.rail); c.fill(); c.restore();
    c.save(); c.globalAlpha = a; c.translate(0, (1 - easeOut(a)) * 24);
    c.shadowColor = 'rgba(0,0,0,.6)'; c.shadowBlur = 40; c.shadowOffsetY = 12;
    rrect(c, x - w / 2, y - h / 2, w, h, 22); c.fillStyle = 'rgb(8,12,18)'; c.fill(); c.shadowBlur = 0; c.shadowOffsetY = 0;
    c.lineWidth = 3; c.strokeStyle = th.trim; c.stroke();
    c.textAlign = 'center'; c.textBaseline = 'middle';
    c.font = '800 38px ' + SERIF; c.fillStyle = th.accent; c.fillText(s.outcome === 'closed' || s.outcome === 'idle' ? 'TABLE CLOSED' : String(s.text || 'TABLE CLOSED').toUpperCase(), x, y - h / 2 + 46);
    c.font = '700 19px ' + SANS; c.fillStyle = th.inkDim;
    c.fillText(s.hands + (s.hands === 1 ? ' hand' : ' hands') + '  ·  wagered ' + fmt(s.total_bet) + ' ' + cur + '  ·  house ' + (s.house_net >= 0 ? '+' : '−') + fmt(Math.abs(s.house_net)), x, y - h / 2 + 88);
    if (!rows.length) { c.font = '700 20px ' + SANS; c.fillStyle = th.ink; c.fillText('Nobody played', x, y + 10); }
    for (var i = 0; i < rows.length; i++) {
      var ry = y - h / 2 + 140 + i * 36, p = rows[i];
      c.textAlign = 'left'; c.font = '800 22px ' + SANS; c.fillStyle = th.ink; c.fillText(fit(c, p.user, '800 %spx ' + SANS, 340, 22, 12), x - w / 2 + 50, ry);
      c.textAlign = 'right'; c.fillStyle = p.net > 0 ? C_WIN : p.net < 0 ? C_LOSE : C_PUSH;
      c.fillText((p.net > 0 ? '+' : p.net < 0 ? '−' : '') + fmt(Math.abs(p.net)), x + w / 2 - 50, ry);
      c.fillStyle = 'rgba(255,255,255,.08)'; c.fillRect(x - w / 2 + 40, ry + 18, w - 80, 1);
    }
    if (more > 0) { c.textAlign = 'center'; c.font = '700 17px ' + SANS; c.fillStyle = th.inkDim; c.fillText('+ ' + more + ' more player' + (more === 1 ? '' : 's'), x, y - h / 2 + 140 + rows.length * 36 + 2); }
    c.restore();
  };

  // ---- the cards that were on the felt fly to the discard tray
  P._ghosts = function (c) {
    var tn = now(), keep = [];
    for (var i = 0; i < this.ghosts.length; i++) {
      var gh = this.ghosts[i], u = (tn - gh.at) / gh.dur;
      if (u >= 1) continue;
      keep.push(gh);
      var p = easeIn(clamp(u, 0, 1)), tx = TRAY_XY.x - gh.w * 0.15, ty = TRAY_XY.y - gh.h * 0.3;
      this._card(c, gh.code, lerp(gh.x, tx, p), lerp(gh.y, ty, p), gh.w, gh.h, { rot: gh.rot + p * 0.6, scale: lerp(1, 0.5, p), alpha: 1 - Math.pow(clamp(u, 0, 1), 3), flip: gh.code ? null : 0 });
    }
    this.ghosts = keep;
  };

  // ---- the frame
  P._frame = function (t) {
    var c = this.ctx, k = this.k, g = this.g, th = this.th;
    this.lastT0 = this.lastT || t; this.lastT = t;
    if (!this.born && this.st) this.born = t;
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, this.canvas.width, this.canvas.height);
    c.setTransform(k, 0, 0, k, 0, 0);
    this.drawn = []; this.fly = [];
    var el = this._elapsed(t), i;
    if (!this.st) return;
    this._drawAvatar(c, g, el, t);
    c.drawImage(this._feltCanvas(), 0, 0, BASE_W, BASE_H);
    var dn = String(g ? g.dealer_name : (this.idle && this.idle.dealer_name) || this.cfg.dealer_name || 'Hex').toUpperCase();
    plaque(c, 960, DEALER.plateY, 250, 30, dn + ' · DEALER', th);
    if (g && g.test) testPlaque(c, 2 * 960 - 330, T.top + 20, 330, 32);
    this._boards(c, g, el, t);
    this._shoeAndTray(c, g, el, t);
    if (g) {
      var pk = g.id + '|' + g.hand_no + '|' + g.phase + '|' + g.round;
      if (g.phase === 'betting' || g.phase === 'action' || g.phase === 'insurance') this._event('go|' + pk, 0, el, 'go');
      if (g.phase === 'settle' && g.last) {
        var anyBj = false, anyWin = false;
        for (i = 0; i < (g.last.results || []).length; i++) { var rr = g.last.results[i]; if (rr.net > 0) anyWin = true; for (var q0 = 0; q0 < rr.hands.length; q0++) if (rr.hands[q0].outcome === 'blackjack') anyBj = true; }
        this._event('res|' + pk, 450, el, anyBj ? 'blackjack' : anyWin ? 'win' : g.last.net > 0 ? 'lose' : 'push');
      }
      this._banner(c, g, el, t);
      this._dealerHand(c, g, el, t);
      for (i = 0; i < g.seats.length; i++) this._seat(c, i, g.seats[i], g, el, t);
      for (i = 0; i < this.fly.length; i++) { var f = this.fly[i]; this._card(c, f[0], f[1], f[2], f[3], f[4], f[5]); }
      this._ghosts(c);
      this._bubble(c, g, t);
      if (g.phase === 'over') this._summary(c, g, el, t);
      this.lastShoe = g.shoe;
      // a blackjack on the table: the confetti of the winners comes from the hands themselves
    } else {
      this._banner(c, null, 0, t);
      // an empty table: the seats' numbers
      for (i = 0; i < this.lay.seats.length; i++) {
        var A = this.lay.seats[i];
        c.save(); c.globalAlpha = 0.4; c.fillStyle = th.ink; c.font = '800 ' + Math.round(this.lay.R * 0.62) + 'px ' + SANS; c.textAlign = 'center'; c.textBaseline = 'middle';
        c.fillText(String(i + 1), A.x, A.y + 1); c.restore();
      }
      this._ghosts(c);
    }
    // forget the cards that left the felt
    if (!this._pruneAt || t - this._pruneAt > 2000) {
      this._pruneAt = t;
      var live = {}; var gg = g;
      if (gg) {
        for (i = 0; i < gg.seats.length; i++) { var s = gg.seats[i]; if (s && s.hands) for (var j2 = 0; j2 < s.hands.length; j2++) for (var q = 0; q < s.hands[j2].cards.length; q++) live[s.hands[j2].uid + ':' + q] = 1; }
        if (gg.dealer) for (i = 0; i < gg.dealer.cards.length; i++) live['d:' + i] = 1;
      }
      for (var kk in this.cardPos) if (has(this.cardPos, kk) && !live[kk]) delete this.cardPos[kk];
    }
  };

  // ------------------------------------------------------------------ sample states (the panel's editor, tests)
  // The same shapes the server's game_view() sends: a sample game in its betting window, and a scripted hand
  // (deal, a few action rounds, the dealer, the payouts) for the editor's "Preview".
  function seatOrder(n) {          // the order free seats are handed out in (as the server does, "center")
    var lo = Math.floor((n - 1) / 2), hi = Math.floor(n / 2), out = [];
    if (lo === hi) { out.push(lo); lo--; hi++; }
    while (lo >= 0 || hi < n) { if (hi < n) { out.push(hi); hi++; } if (lo >= 0) { out.push(lo); lo--; } }
    return out;
  }
  var NAMES = ['alice', 'bob', 'carol', 'dave', 'erin', 'frank', 'grace', 'heidi', 'ivan', 'judy', 'mallory', 'niaj', 'olivia', 'peggy'];
  // [cards, bet, rounds]: a round is [action, card?] for the hand, or [[action, card], ...] one per hand after a split
  var ARCH = [
    { c: ['KH', 'QD'], bet: 100, r: [['stand']] },
    { c: ['AS', 'KC'], bet: 50, r: [] },
    { c: ['8S', '8H'], bet: 25, r: [['split', '3D', '9S'], [['double', 'TC'], ['stand']]] },
    { c: ['9C', '7D'], bet: 10, r: [['hit', '6S']] },
    { c: ['5D', '6C'], bet: 200, r: [['double', '9H']] },
    { c: ['TD', '7S'], bet: 75, r: [['stand']] },
    { c: ['AD', '5H'], bet: 40, r: [['hit', '4C'], ['stand']] },
    { c: ['4C', '5S'], bet: 60, r: [['hit', '3D'], ['hit', '5H'], ['stand']] },
    { c: ['2C', '3D'], bet: 20, r: [['hit', '2S'], ['hit', '4H'], ['hit', '3S'], ['hit', '5D'], ['stand']] }
  ];

  function mkHand(uid, cards, bet, o) {
    var v = handValue(cards), h = { uid: uid, cards: cards.slice(), bet: bet, pending: 0, total: v.total, soft: v.soft, status: 'active',
      doubled: false, split: false, act: null, result: null };
    if (o) for (var k in o) if (has(o, k)) h[k] = o[k];
    if (cards.length === 2 && v.total === 21 && !h.split) h.status = 'blackjack';
    else if (v.total > 21) h.status = 'bust';
    else if (v.total === 21 && h.status === 'active') h.status = 'stand';
    return h;
  }
  function refresh(h) { var v = handValue(h.cards); h.total = v.total; h.soft = v.soft; if (v.total > 21) h.status = 'bust'; else if (v.total === 21 && h.status === 'active') h.status = 'stand'; return h; }
  function settleOf(h, dealer, pays) {
    var bet = h.bet, st = h.status, win = bet * 2, oc, pay;
    if (st === 'blackjack') { if (dealer.bj) { oc = 'push'; pay = bet; } else { oc = 'blackjack'; pay = bet + Math.floor(bet * 3 / 2); } }
    else if (st === 'bust') { oc = 'bust'; pay = 0; }
    else if (dealer.bj) { oc = 'lose'; pay = 0; }
    else if (dealer.bust || h.total > dealer.total) { oc = 'win'; pay = win; }
    else if (h.total === dealer.total) { oc = 'push'; pay = bet; }
    else { oc = 'lose'; pay = 0; }
    return { outcome: oc, pay: pay, net: pay - bet };
  }

  function mkShoe(decks, dealt, onFelt, cutPct) {
    var size = decks * 52, cut = Math.floor(size * (cutPct || 75) / 100);
    return { decks: decks, size: size, left: size - dealt, dealt: dealt, discards: Math.max(0, dealt - onFelt), cut_at: cut, to_cut: Math.max(0, cut - dealt),
      cut_passed: dealt >= cut, next_decks: decks, shuffles: 1, penetration_pct: cutPct || 75, shuffled: false };
  }
  function rulesOf(c) {
    return { dealer_hits_soft_17: !!c.dealer_hits_soft_17, dealer_peeks: c.dealer_peeks !== false, blackjack_pays: c.blackjack_pays || '3:2', double_on: c.double_on || 'any',
      double_after_split: c.double_after_split !== false, max_hands: c.max_hands || 4, resplit_aces: !!c.resplit_aces, split_aces_one_card: c.split_aces_one_card !== false,
      insurance: c.insurance !== false, surrender: !!c.surrender, five_card_charlie: !!c.five_card_charlie, edge_pct: 0.45, text: [] };
  }
  function roundState(game, idle) {
    return { state: game ? game.phase : 'idle', visible: true, spin: null, last: null, history: [], busy_ms: 0, announce: null, table: null,
      game: game, idle: idle || {}, preview: null };
  }
  function idleOf(c) {
    return { currency: c.currency || 'hexcoins', seats: c.seats || 10, dealer_name: c.dealer_name || 'Hex', rules: rulesOf(c), min_bet: c.min_bet || 1, max_bet: c.max_bet || 0, commands_text: c.commands_text || '' };
  }

  function baseGame(c, gid, phase, N, seats, extra) {
    var g = { id: gid, test: false, phase: phase, ends_in_ms: null, phase_ms: 0, elapsed_ms: 0, hand_no: 1, round: 0, rounds_max: c.max_rounds || 8, closing: false,
      seat_count: N, seats: seats, queue: [], dealer: { name: c.dealer_name || 'Hex', cards: [], total: 0, soft: false, hidden: false, bj: null, bust: false, peeked: null, up: null },
      deal: null, shoe: mkShoe(6, 40, 0), say: null, rules: rulesOf(c), players: [], at_risk: 0, last: null, outcome: null, summary: null,
      currency: c.currency || 'hexcoins', min_bet: c.min_bet || 1, max_bet: c.max_bet || 0, commands_text: c.commands_text || '', dealer_name: c.dealer_name || 'Hex', seats_free: 0 };
    if (extra) for (var k in extra) if (has(extra, k)) g[k] = extra[k];
    return g;
  }
  function emptySeats(N) { var a = []; for (var i = 0; i < N; i++) a.push({ n: i + 1, user: null, state: 'empty' }); return a; }

  // a table in its betting window with `np` players (and a short queue)
  function sample(c, gid, ms, opts) {
    c = merge(DEFAULTS, c); opts = opts || {};
    var N = clamp(Math.round(+c.seats || 10), 1, 14), np = clamp(opts.players || Math.min(N, 7), 0, N), seats = emptySeats(N), order = seatOrder(N).slice(0, np), i;
    var bets = [100, 50, 25, 10, 200, 75, 40, 500, 5, 1000, 20, 60, 150, 2500];
    for (i = 0; i < order.length; i++) {
      var si = order[i], held = opts.held && i === order.length - 1;
      seats[si] = { n: si + 1, user: NAMES[i % NAMES.length], state: held ? 'held' : 'bet', bet: held ? 0 : bets[i % bets.length], stake: held ? 0 : bets[i % bets.length], ins: 0, ins_state: null,
        leaving: false, last_bet: bets[i % bets.length], hands: [] };
    }
    var queue = opts.queue == null ? (np >= N ? [{ user: 'walter', bet: 50 }, { user: 'xena', bet: 25 }, { user: 'yuri', bet: 100 }] : []) : opts.queue;
    var g = baseGame(c, gid, 'betting', N, seats, { ends_in_ms: ms, phase_ms: Math.max(ms, 15000), hand_no: 2, queue: queue, shoe: mkShoe(6, 120, 0),
      say: { id: 1, key: 'next', text: 'Next hand - ante up to keep your seat.', phase: 'betting' }, seats_free: N - np });
    g.players = order.map(function (si, k) { return { user: seats[si].user, seat: si + 1, stake: seats[si].bet, bet: seats[si].bet, status: 'ready', hands: 0, net: 0, queued: false }; });
    return roundState(g, idleOf(c));
  }

  // [{ms, state}] a whole hand
  function demo(c, gid, opts) {
    c = merge(DEFAULTS, c); opts = opts || {};
    var N = clamp(Math.round(+c.seats || 10), 1, 14), np = clamp(opts.players || Math.min(6, N), 1, N), step = clamp(+c.card_ms || 240, 100, 800);
    var order = seatOrder(N).slice(0, np).sort(function (a, b) { return a - b; }), uid = 1, i, j, steps = [], idle = idleOf(c), dealerCards = opts.dealer || ['TS', '7D', null];
    var P = order.map(function (si, p) {
      var a = ARCH[(opts.arch ? opts.arch[p] : p) % ARCH.length];
      return { si: si, n: si + 1, user: (opts.names && opts.names[p]) || NAMES[p % NAMES.length], bet: a.bet, a: a, hands: [mkHand(uid++, a.c, a.bet)], r: 0, ins: 0 };
    });
    var dealt = 110;
    function seatsView(phase) {
      var arr = emptySeats(N);
      P.forEach(function (p) {
        var stake = 0; p.hands.forEach(function (h) { stake += h.bet + h.pending; });
        arr[p.si] = { n: p.n, user: p.user, state: phase === 'betting' ? 'bet' : phase === 'settle' ? 'done' : 'playing', bet: p.bet, stake: phase === 'betting' ? p.bet : stake + p.ins,
          ins: p.ins, ins_state: p.ins ? 'taken' : null, leaving: false, last_bet: p.bet, hands: phase === 'betting' ? [] : p.hands.map(function (h) { return JSON.parse(JSON.stringify(h)); }) };
      });
      return arr;
    }
    function dealerView(reveal) {
      var cards = dealerCards.slice(0, 2).map(function (cd, k) { return k === 1 && !reveal ? null : cd; });
      var extra = reveal ? dealerCards.slice(2).filter(Boolean) : [];
      cards = cards.concat(extra);
      var vis = cards.filter(Boolean), v = handValue(vis);
      return { name: c.dealer_name || 'Hex', cards: cards, total: v.total, soft: v.soft && reveal, hidden: !reveal, bj: reveal ? (vis.length === 2 && v.total === 21) : false, bust: v.total > 21, peeked: true, up: cards[0] };
    }
    function push(phase, ms, g, extra) {
      var game = baseGame(c, gid, phase, N, seatsView(phase), Object.assign({ ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0, dealer: dealerView(false), shoe: mkShoe(6, dealt, 0), seats_free: N - np }, g || {}));
      steps.push({ ms: ms, state: roundState(game, idle) });
      return game;
    }
    // 1. bets
    var s1 = sample(c, gid, 3000, { players: np });
    // (the same players the hands will use)
    var seats0 = seatsView('betting'); s1.game.seats = seats0; s1.game.queue = [];
    s1.game.say = { id: 2, key: 'open', text: 'Place your bets, folks.', phase: 'betting' };
    steps.push({ ms: 3000, state: s1 });
    // 2. the deal
    var order1 = [], k;
    for (k = 0; k < 2; k++) { P.forEach(function (p) { order1.push([p.n, 0, k]); }); order1.push([0, 0, k]); }
    var t0 = 350, dealMs = t0 + order1.length * step + 650;
    dealt += order1.length;
    push('dealing', dealMs, { deal: { kind: 'deal', t0: t0, step: step, order: order1, moves: [], flip: null, shuffle: 0 }, shoe: mkShoe(6, dealt, order1.length),
      say: { id: 3, key: 'deal', text: 'Good luck, everyone.', phase: 'dealing' }, hand_no: 1 });
    // 3. the action rounds
    var round = 0, again = true;
    while (again && round < 6) {
      round++;
      var plan = {}, any = false;
      P.forEach(function (p) {
        var rd = p.a.r[p.r];
        if (!rd) return;
        var entries = Array.isArray(rd[0]) ? rd : [rd];
        p.hands.forEach(function (h, hi) { if (h.status === 'active' && entries[hi]) { h.act = entries[hi][0] === 'split' ? 'split' : entries[hi][0]; if (h.act === 'split') h.pending = h.bet; any = true; plan[p.n + ':' + hi] = entries[hi]; } });
      });
      if (!any) break;
      push('action', round === 1 ? 4200 : 2800, { round: round, hand_no: 1, shoe: mkShoe(6, dealt, 2 * np + 2),
        say: round === 1 ? { id: 4, key: 'action', text: 'Hit or stand? Make your move.', phase: 'action' } : null });
      // resolve the round in seat order
      var order2 = [], moves = [];
      P.forEach(function (p) {
        var rd = p.a.r[p.r]; if (!rd) return;
        var entries = Array.isArray(rd[0]) ? rd : [rd], hi = 0;
        p.r++;
        var hands = p.hands.slice();
        hands.forEach(function (h) {
          var idx = p.hands.indexOf(h), e = entries[hands.indexOf(h)];
          if (!e || h.status !== 'active') return;
          var act = e[0];
          h.act = null;
          if (act === 'stand') h.status = 'stand';
          else if (act === 'hit' || act === 'double') {
            h.cards.push(e[1]); order2.push([p.n, idx, h.cards.length - 1]);
            if (act === 'double') { h.bet *= 2; h.doubled = true; if (h.status !== 'bust') h.status = 'stand'; }
            refresh(h);
            if (act === 'double' && h.status === 'active') h.status = 'stand';
          } else if (act === 'split') {
            var second = h.cards.pop(), nh = mkHand(uid++, [second], h.pending, { split: true });
            h.pending = 0; h.split = true;
            p.hands.splice(idx + 1, 0, nh);
            moves.push([p.n, idx, idx + 1]);
            h.cards.push(e[1]); nh.cards.push(e[2]);
            order2.push([p.n, idx, 1], [p.n, idx + 1, 1]);
            refresh(h); refresh(nh);
            h.act = null;
          }
        });
        // hands that had no entry this round (e.g. finished) stay as they are
      });
      dealt += order2.length;
      var rms = 300 + order2.length * Math.max(110, step * 0.85) + 500;
      push('resolve', rms, { round: round, deal: { kind: 'hit', t0: 300, step: Math.max(110, Math.round(step * 0.85)), order: order2, moves: moves, flip: null, shuffle: 0 },
        shoe: mkShoe(6, dealt, 2 * np + 2 + order2.length) });
      again = P.some(function (p) { return p.hands.some(function (h) { return h.status === 'active'; }); });
      if (again) P.forEach(function (p) { p.hands.forEach(function (h) { if (h.status === 'active' && !p.a.r[p.r]) h.status = 'stand'; }); });
      again = P.some(function (p) { return p.hands.some(function (h) { return h.status === 'active'; }); });
    }
    P.forEach(function (p) { p.hands.forEach(function (h) { if (h.status === 'active') h.status = 'stand'; }); });
    // 4. the dealer
    var dv = dealerView(true), drawn = dealerCards.slice(2).filter(Boolean), ordD = [];
    for (k = 0; k < drawn.length; k++) ordD.push([0, 0, 2 + k]);
    dealt += drawn.length;
    var dms = 650 + ordD.length * Math.max(450, step * 3) + 750;
    var gD = push('dealer', dms, { deal: { kind: 'dealer', t0: 650, step: Math.max(450, step * 3), order: ordD, moves: [], flip: 1, shuffle: 0 }, dealer: dv,
      shoe: mkShoe(6, dealt, 0) });
    gD.dealer.hidden = false;
    // 5. the payouts
    var dealerFinal = { total: dv.total, bust: dv.total > 21, bj: dv.bj }, results = [], net = 0;
    P.forEach(function (p) {
      var row = { user: p.user, seat: p.n, hands: [], net: 0 };
      p.hands.forEach(function (h) { h.result = settleOf(h, dealerFinal); row.net += h.result.net; row.hands.push({ outcome: h.result.outcome, bet: h.bet, pay: h.result.pay }); });
      net += row.net; results.push(row);
    });
    var g5 = push('settle', 7000, { dealer: dv, last: { hand_no: 1, dealer: { cards: dv.cards, total: dv.total, bust: dv.bust, bj: dv.bj }, results: results, net: net },
      say: { id: 5, key: net < 0 ? 'house' : 'players', text: net < 0 ? 'The house takes this one.' : 'Pay the winners.', phase: 'settle' }, shoe: mkShoe(6, dealt, 0) });
    g5.dealer.hidden = false;
    return steps;
  }

  // ------------------------------------------------------------------ registration
  function copyObj(o) { var r = {}; for (var k in o) if (has(o, k)) r[k] = o[k]; return r; }
  var API = {
    BASE_W: BASE_W, BASE_H: BASE_H, THEMES: THEMES, DEFAULTS: copyObj(DEFAULTS), APPEARANCE: APPEARANCE.slice(),
    create: function (container, config, opts) { return new BJ(container, config, opts); },
    // for the panel's editor (and tests): a sample table in its betting window, a scripted hand, helpers
    sample: sample, demo: demo, roundState: roundState, handValue: handValue, seatLayout: seatLayout, seatOrder: seatOrder, chipsFor: chipsFor
  };
  HG.blackjack = API;
  if (typeof module === 'object' && module && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
