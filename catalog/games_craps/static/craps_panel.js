/*
 * Hexcast Games — Craps panel tab                       craps_panel.js
 *
 * Builds the whole Craps tab of the Games panel (/games -> section#tab-craps):
 *   1. OBS browser source      both overlay URLs + Copy
 *   2. Craps table             live mirror (real renderer, sound off), Roll / Show / Hide,
 *                              Start / Cancel timer, state + countdown, phase / point /
 *                              shooter / hand / auto-roll,
 *                              last roll; Edit Mode -> click -> placement & look editor
 *   3. On the table            every bet riding, take-down (confirm), refund all & clear
 *   4. Place a bet             validate (dry run) + place (REAL: writes ledger debits)
 *   5. Hex display             "Hex does the math": Hex's payouts card (/announce) and board
 *                              (/board) — display only, never bets / ledger / history
 *   6. Ledger                  live tail (panel socket) + GET /ledger, per-user + house net
 *                              (craps events only: the games ledger is shared with roulette)
 *   7. History & stats         last 20 rolls, totals vs expected, points / sevens / hands
 *   8. Settings                every non-appearance key of config.craps
 *   9. API                     endpoints, bet syntax, curl examples, hooking up the bank
 *
 * Plain browser script, no modules / deps / build step. games_panel.html loads it right after the
 * craps.js renderer (window.HexGames.craps) when Craps is installed; what the page shares (toast, copy,
 * the Hex display helpers ...) comes from window.GamesPage. Everything lives inside one closure so
 * nothing clashes with the other games' tabs.
 *
 * The page dispatches its /games/ws/panel socket to us through window.GamePanels.craps:
 *   onConfig(config)  {"type":"config"}   -> config.craps
 *   onState(state)    {"type":"state","game":"craps"}   (STATE carries `table`)
 *   onStop(game)      {"type":"stop"[,"game"]}
 *   onLedger(events)  {"type":"ledger","game":"craps","events":[...]}
 *   onLink(bool)      socket up / down
 *   onTab(key)        a game tab was selected
 *   onRemove()        Craps is being uninstalled: stop the timers, close the editor, free the renderer
 * and keeps the last config / state / link in window.GamesPage.last so we can catch up.
 */
(function () {
  'use strict';

  var HOST = document.getElementById('tab-craps');
  if (!HOST) return;

  var GP = window.GamesPage || {};
  var API = '/games/api/craps';

  // ======================================================================
  // constants (CRAPS_SPEC §5)
  // ======================================================================
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
  // The only keys a roll's `overrides` may carry — and what the Edit-Mode editor edits.
  var APPEARANCE = ['x', 'y', 'scale', 'theme', 'dice_style', 'show_point', 'show_history', 'history_count',
    'show_user', 'show_bets', 'bets_max', 'show_payouts', 'payouts_max', 'sfx', 'sfx_volume', 'show_rules'];
  var EDIT_KEYS = APPEARANCE;
  var THEMES = [['classic', 'Classic — casino green'], ['neon', 'Neon — black & Hexcast red'],
    ['midnight', 'Midnight — navy'], ['royal', 'Royal — purple & gold']];
  var DICE = [['red', 'Red — translucent casino red'], ['white', 'White — ivory'], ['black', 'Black'], ['gold', 'Gold']];
  var ODDS_RULES = [['345', '3-4-5× (casino standard)'], ['1', '1× odds'], ['2', '2× odds'],
    ['3', '3× odds'], ['5', '5× odds'], ['10', '10× odds'], ['20', '20× odds'], ['100', '100× odds']];
  var WAYS = { 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1 };   // of 36
  var WORD = { 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine',
    10: 'ten', 11: 'eleven', 12: 'twelve' };
  var PIPS = { 1: [4], 2: [2, 6], 3: [2, 4, 6], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8] };
  var LINE_TYPES = { pass: 1, dont_pass: 1, come: 1, dont_come: 1 };   // bets that can carry odds
  var REASONS = { bet: 'bet', add: 'added to bet', odds: 'odds', win: 'win', win_stays: 'win · bet stays up',
    push: 'push', returned: 'odds returned', remove: 'taken down', refund: 'refund · table cleared' };
  var LEDGER_MAX = 10000, LEDGER_PAGE = 5000, LEDGER_ROWS = 300;

  // ======================================================================
  // helpers (same behaviour as the page's)
  // ======================================================================
  // Looked up inside our own section, not the whole document: once Craps is removed the section is
  // detached, and a late timer or fetch reply must still find its elements instead of throwing.
  function $(id) { return HOST.querySelector('#' + id); }
  var esc = typeof GP.esc === 'function' ? GP.esc : function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var toast = typeof GP.toast === 'function' ? GP.toast : function (msg) { console.log('[craps]', msg); };
  var copyText = typeof GP.copyText === 'function' ? GP.copyText : function (s) {
    try { navigator.clipboard.writeText(s); toast('Copied'); } catch (e) { toast('Copy failed'); }
  };
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function num(v, d) { v = parseFloat(v); return isFinite(v) ? v : d; }
  function pick(o, keys) { var r = {}; keys.forEach(function (k) { if (o && o[k] !== undefined) r[k] = o[k]; }); return r; }
  function noop() {}
  function safe(fn) { try { return fn(); } catch (e) { console.error('[craps] renderer error:', e); } }
  function isObj(o) { return !!o && typeof o === 'object' && !Array.isArray(o); }
  // User names are chat input: "constructor" or "__proto__" must never hit Object.prototype.
  function dict() { return Object.create(null); }
  function own(o, k) { return !!o && Object.prototype.hasOwnProperty.call(o, k); }
  function C() { return (window.HexGames && window.HexGames.craps) || null; }
  function baseW() { var c = C(); return (c && +c.BASE_W) || 720; }
  function baseH() { var c = C(); return (c && +c.BASE_H) || 405; }
  function editMode() { return document.body.classList.contains('edit-mode'); }
  function tabShown() { return HOST.classList.contains('sel'); }
  function fmtN(v) { v = +v || 0; return (Math.round(v * 100) / 100).toLocaleString(); }
  function coins(v) { return fmtN(v) + ' ' + cur(); }
  function signed(v) { v = Math.round(+v || 0); return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toLocaleString(); }
  function fmtSec(ms) {
    ms = Math.max(0, ms);
    if (ms >= 60000) { var s = Math.ceil(ms / 1000); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
    return (ms / 1000).toFixed(1) + 's';
  }
  function fmtClock(ms) { var s = Math.max(0, Math.ceil(ms / 1000)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
  // Same cleaning as the server's _clean_user(): trim, drop leading @, trim, 40 chars (case kept).
  function cleanUser(v) { return String(v == null ? '' : v).trim().replace(/^@+/, '').trim().slice(0, 40); }
  function rolling(s) { return s === 'spinning' || s === 'rolling'; }
  function stateName(s) { return rolling(s) ? 'rolling' : (s || 'idle'); }
  function plural(n, w) { return n + ' ' + w + (n === 1 ? '' : 's'); }

  // ---- dice ----
  function dieHTML(n, cls) {
    n = +n;
    var on = PIPS[n] || [], cells = '';
    for (var i = 0; i < 9; i++) cells += on.indexOf(i) >= 0 ? '<i class="p"></i>' : '<i></i>';
    return '<span class="cr-die ' + esc(diceStyle()) + (cls ? ' ' + cls : '') + '" aria-label="' + (PIPS[n] ? n : '?') + '">' + cells + '</span>';
  }
  function diceOf(r) {
    if (r && Array.isArray(r.dice) && r.dice.length === 2) return [+r.dice[0], +r.dice[1]];
    if (r && typeof r.label === 'string' && /^\d-\d$/.test(r.label)) return r.label.split('-').map(Number);
    return [0, 0];
  }
  function totalOf(r) { var d = diceOf(r); return r && r.total != null ? +r.total : d[0] + d[1]; }

  // Local RESULT (spec §2) — only a fallback for the editor's demo roll when the
  // renderer has no resultFor(). The server owns the real calls.
  function localResult(d1, d2, phase, point) {
    var t = d1 + d2, pair = d1 === d2, hard = pair && (t === 4 || t === 6 || t === 8 || t === 10);
    var r = { dice: [d1, d2], total: t, pair: pair, hard: hard, number: String(t), label: d1 + '-' + d2,
      phase_before: phase === 'point' ? 'point' : 'come_out', point_before: phase === 'point' ? point : null };
    var named = { 2: 'ACES', 3: 'ACE-DEUCE', 11: 'YO-LEVEN', 12: 'BOXCARS' };
    r.phase_after = r.phase_before; r.point_after = r.point_before;
    if (r.phase_before === 'come_out') {
      if (t === 7 || t === 11) { r.event = 'natural'; r.call = t === 7 ? 'SEVEN · WINNER' : 'YO-LEVEN'; r.sub = 'Front line winner'; }
      else if (t === 2 || t === 3 || t === 12) { r.event = 'craps'; r.call = named[t]; r.sub = t === 12 ? 'Craps · bar the 12' : 'Craps · line away'; }
      else {
        r.event = 'point_set'; r.call = 'POINT IS ' + t; r.phase_after = 'point'; r.point_after = t;
        var sw = (t === 5 || t === 9 ? '' : (pair ? 'hard ' : 'easy ')) + WORD[t] + ' · mark it';
        r.sub = sw.charAt(0).toUpperCase() + sw.slice(1);
      }
    } else if (t === point) { r.event = 'point_made'; r.call = 'WINNER ' + t; r.sub = 'Pay the line'; r.phase_after = 'come_out'; r.point_after = null; }
    else if (t === 7) { r.event = 'seven_out'; r.call = 'SEVEN OUT'; r.sub = 'Line away · don\'t pass wins'; r.phase_after = 'come_out'; r.point_after = null; }
    else {
      r.event = 'roll';
      r.call = named[t] || ((t === 4 || t === 6 || t === 8 || t === 10) ? (hard ? 'HARD ' : 'EASY ') + WORD[t].toUpperCase() : WORD[t].toUpperCase());
      r.sub = 'Point is ' + point;
    }
    return r;
  }
  function resultFor(d1, d2, phase, point) {
    var c = C(), r = null;
    if (c && typeof c.resultFor === 'function') r = safe(function () { return c.resultFor(d1, d2, phase, point); });
    return r || localResult(d1, d2, phase, point);
  }

  // ======================================================================
  // HTTP — busy / bets_closed toasts speak dice
  // ======================================================================
  var BUSY_WHAT = { spinning: 'the dice are rolling', rolling: 'the dice are rolling',
    result: 'the last roll is still on screen', cooldown: 'the table is cooling down' };
  function busyText(d, verb) {
    var secs = Math.max(1, Math.ceil((+(d && d.retry_in_ms) || 0) / 1000));
    var what = (d && d.error === 'bets_closed') ? 'bets are closed while the dice are rolling'
      : (BUSY_WHAT[d && d.state] || 'the dice are rolling');
    return (verb || 'Busy') + ' — ' + what + '. Try again in ' + secs + 's';
  }
  function errText(res) {
    var d = res.d || {};
    var e = (typeof d.error === 'string' && d.error) || (typeof d.detail === 'string' && d.detail) || ('Request failed (HTTP ' + res.status + ')');
    // "unknown game" / a bare 404 on a craps route = a Hexcast started before craps existed
    if (e === 'unknown game' || res.status === 404) { noBackend(true); return "Craps isn't loaded on this Hexcast yet — restart it"; }
    return e;
  }
  function isBusy(res) { return res.status === 409 || res.d.error === 'busy' || res.d.error === 'bets_closed'; }
  // Raw request: never toasts. {net, status, ok, d}
  async function req(method, path, body) {
    var opt = { method: method };
    if (body !== undefined) { opt.headers = { 'Content-Type': 'application/json' }; opt.body = JSON.stringify(body); }
    var r, d = {};
    try { r = await fetch(path, opt); } catch (e) { return { net: true, status: 0, ok: false, d: {} }; }
    try { d = (await r.json()) || {}; } catch (e) { d = {}; }
    if (!isObj(d)) d = {};
    return { net: false, status: r.status, ok: r.ok && d.ok !== false, d: d };
  }
  // Like the page's api(): toasts on failure, returns the body or null.
  async function api(method, path, body, verb) {
    var res = await req(method, path, body);
    if (res.net) { toast('Network error — is Hexcast running?', 2400); return null; }
    if (isBusy(res)) { toast(busyText(res.d, verb), 3000); return null; }
    if (!res.ok) { toast(errText(res), 2600); return null; }
    return res.d;
  }

  // ======================================================================
  // styles (cr- prefix; page variables/classes reused)
  // ======================================================================
  var CSS = `
  #tab-craps .cr-banner{display:none;margin-bottom:16px;padding:12px 16px;border-radius:12px;border:1px solid #4d3a10;
     background:#211a0b;color:var(--warn);font-size:14px}
  #tab-craps .cr-banner.on{display:block}
  #tab-craps .cr-banner b{color:#ffd27a}
  .gwheel.cr-box.empty{border-radius:22px}
  #tab-craps .two > .card,#tab-craps .game > *{min-width:0}   /* wide tables scroll inside their card */
  .cr-readout{font:800 30px/1 var(--mono);color:var(--ink);font-variant-numeric:tabular-nums}
  .cr-state{font:600 12px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--dim)}
  .cr-state.rolling{color:var(--gold)} .cr-state.result{color:var(--good)} .cr-state.cooldown{color:var(--warn)}
  .cr-state.betting{color:#4ea1ff}
  .cr-sub{font-size:12px;color:var(--dim);margin-top:4px;min-height:18px}
  .cr-facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:12px}
  .cr-facts > div{background:#0c0c11;border:1px solid var(--line);border-radius:8px;padding:6px 9px;min-width:0}
  .cr-facts .lbl{display:block;font-size:10px;color:var(--dim);letter-spacing:.1em;text-transform:uppercase;font-weight:600}
  .cr-facts b{display:flex;align-items:center;gap:6px;min-height:20px;font:700 13px/1.3 var(--mono);color:var(--ink);
     white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .cr-facts b span.t{overflow:hidden;text-overflow:ellipsis}
  .cr-facts b.warn{color:var(--warn)} .cr-facts b.good{color:var(--good)} .cr-facts b.dim{color:var(--dim)}
  .cr-puck{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;
     font:800 6.5px/1 var(--mono);letter-spacing:.02em;background:#141414;color:#ddd;border:2px solid #4a4a4a}
  .cr-puck.on{background:#f3f1ea;color:#111;border-color:#b9b4a6}
  #cr-card-table .row input{flex:1 1 150px;min-width:0;width:auto}
  .cr-last{display:flex;align-items:center;gap:12px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;
     background:#0c0c11;min-height:66px}
  .cr-last .none{color:var(--dim);font-size:13px}
  .cr-last .dd{display:flex;gap:6px;flex:0 0 auto}
  .cr-last .meta{display:flex;flex-direction:column;gap:2px;min-width:0}
  .cr-last .call{font:800 14px var(--mono);letter-spacing:.06em;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .cr-last .call em{font-style:normal;color:var(--gold);margin-right:6px}
  .cr-last .who{font-size:12px;color:var(--dim);overflow-wrap:anywhere}
  body.edit-mode #cr-edit-place{background:#3a2a18;border-color:var(--amber);color:var(--amber-soft)}
  body.edit-mode #cr-card-table{border-color:var(--amber);cursor:pointer;position:relative}
  body.edit-mode #cr-card-table::after{content:"✎";position:absolute;top:12px;right:16px;color:var(--amber-soft);
     text-shadow:0 1px 2px #000;font-size:16px}
  body.edit-mode #cr-card-table:hover{border-color:var(--amber-soft)}

  /* dice (CSS pips; colours follow dice_style) */
  .cr-die{display:inline-grid;grid-template-columns:repeat(3,1fr);grid-template-rows:repeat(3,1fr);flex:0 0 auto;
     width:40px;height:40px;padding:6px;border-radius:9px;vertical-align:middle;
     background:linear-gradient(145deg,#e4323a,#a3151b);
     box-shadow:inset 0 0 0 1px rgba(255,255,255,.28),inset -2px -3px 5px rgba(0,0,0,.28),0 2px 6px rgba(0,0,0,.55)}
  .cr-die i{display:block;border-radius:50%}
  .cr-die i.p{background:radial-gradient(circle at 45% 40%,#fff 0 52%,rgba(255,255,255,0) 60%)}
  .cr-die.white{background:linear-gradient(145deg,#fbf8ef,#d5cfbd)}
  .cr-die.white i.p{background:radial-gradient(circle at 45% 40%,#15151a 0 52%,rgba(0,0,0,0) 60%)}
  .cr-die.black{background:linear-gradient(145deg,#34343a,#0d0d10);box-shadow:inset 0 0 0 1px rgba(255,255,255,.18),0 2px 6px rgba(0,0,0,.55)}
  .cr-die.gold{background:linear-gradient(145deg,#f4d98a,#b4832a)}
  .cr-die.gold i.p{background:radial-gradient(circle at 45% 40%,#2a1d05 0 52%,rgba(0,0,0,0) 60%)}
  .cr-die.sm{width:19px;height:19px;padding:2.5px;border-radius:4.5px;box-shadow:inset 0 0 0 1px rgba(255,255,255,.22),0 1px 2px rgba(0,0,0,.5)}

  /* history strip */
  .cr-hist{display:flex;flex-wrap:wrap;gap:6px;min-height:52px}
  .cr-hist:empty::after{content:'No rolls yet.';color:var(--dim);font-size:13px;align-self:center}
  .cr-roll{display:inline-flex;flex-direction:column;align-items:center;gap:3px;padding:5px 6px 4px;border-radius:9px;
     border:1px solid var(--line);background:#0c0c11;min-width:50px}
  .cr-roll .dd{display:flex;gap:3px}
  .cr-roll b{font:800 12px/1 var(--mono);color:var(--ink);font-variant-numeric:tabular-nums}
  .cr-roll.seven{border-color:var(--red-deep);background:#1f0f10} .cr-roll.seven b{color:var(--red-soft)}
  .cr-roll.win{border-color:#1d4d33;background:#0e1a14} .cr-roll.win b{color:var(--good)}
  .cr-roll.point{border-color:#6b5a1f} .cr-roll.point b{color:var(--gold)}
  .cr-roll.first{box-shadow:0 0 0 2px var(--gold)}

  /* totals histogram: one hue, expected shown as a tick */
  .cr-hgrid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:18px;margin-top:16px}
  .cr-histo{position:relative;display:grid;grid-template-columns:repeat(11,minmax(0,1fr));gap:4px;height:132px;
     margin-left:30px;border-bottom:1px solid var(--line-hi, #35354a)}
  .cr-histo .gl{position:absolute;left:0;right:0;height:0;border-top:1px solid rgba(255,255,255,.05);pointer-events:none}
  .cr-histo .gl span{position:absolute;right:calc(100% + 6px);top:-7px;font:600 10px/1 var(--mono);color:var(--dim)}
  .cr-histo .col{position:relative;height:100%;display:flex;align-items:flex-end;justify-content:center;cursor:default}
  .cr-histo .col:hover{background:rgba(255,255,255,.04)}
  .cr-histo .bar{width:72%;max-width:28px;background:#4ea1ff;border-radius:4px 4px 0 0}
  .cr-histo .exp{position:absolute;left:10%;right:10%;height:2px;background:var(--ink);opacity:.75;border-radius:1px}
  .cr-hx{display:grid;grid-template-columns:repeat(11,minmax(0,1fr));gap:4px;text-align:center;
     font:700 11px var(--mono);color:var(--dim);margin:5px 0 0 30px}
  .cr-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--dim);margin-top:8px}
  .cr-legend i{display:inline-block;vertical-align:middle;margin-right:5px}
  .cr-legend i.b{width:10px;height:10px;border-radius:2px;background:#4ea1ff}
  .cr-legend i.e{width:14px;height:2px;background:var(--ink);opacity:.75}
  .cr-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
  .cr-stat{background:#0c0c11;border:1px solid var(--line);border-radius:8px;padding:6px 10px;min-width:0}
  .cr-stat .lbl{display:block;font-size:10px;color:var(--dim);letter-spacing:.1em;text-transform:uppercase;font-weight:600}
  .cr-stat b{font:800 17px/1.25 var(--mono);color:var(--ink);font-variant-numeric:tabular-nums}
  .cr-stat small{color:var(--dim);font-size:11px;margin-left:6px}

  /* on the table */
  table.cr-bets{width:100%;border-collapse:collapse;font-size:13px}
  table.cr-bets th{text-align:left;font:600 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--dim);
     padding:4px 8px 6px;border-bottom:1px solid var(--line);white-space:nowrap}
  table.cr-bets td{padding:6px 8px;border-top:1px solid var(--line);vertical-align:middle}
  table.cr-bets td.num,table.cr-bets th.num{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
  table.cr-bets td.act{text-align:right;white-space:nowrap}
  table.cr-bets .cr-acts{display:inline-flex;flex-wrap:wrap;justify-content:flex-end;gap:5px;vertical-align:middle}
  table.cr-bets tr.u td{background:#0f0f16;border-top:1px solid var(--line-hi, #35354a)}
  table.cr-bets tr.u .uh{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
  table.cr-bets tr.u .who{font-weight:700;color:var(--ink);overflow-wrap:anywhere}
  table.cr-bets tr.u .n{color:var(--dim);font-size:12px;margin-left:6px}
  table.cr-bets .bl{color:var(--ink)}
  table.cr-bets .bl .tg{margin-left:6px}
  .cr-empty{padding:18px 8px;color:var(--dim);font-size:13px;text-align:center}
  .cr-tag{display:inline-block;font:700 10px/1 var(--mono);letter-spacing:.08em;padding:3px 6px;border-radius:99px;
     border:1px solid var(--line);color:var(--dim);white-space:nowrap;vertical-align:1px}
  .cr-tag.on{color:var(--good);border-color:#1d4d33;background:#0e2118}
  .cr-tag.off{color:var(--warn);border-color:#4d3a10;background:#211a0b}
  .cr-tag.real{color:var(--red-soft);border-color:var(--red-deep);background:#2a1414}
  button.cr-mini{padding:4px 9px;font:600 12px/1.3 inherit;border-radius:7px;background:#1c1c25;color:var(--ink);
     border:1px solid var(--line);cursor:pointer;white-space:nowrap}
  button.cr-mini:hover{border-color:var(--accent)}
  button.cr-mini.yes{background:#3a1414;border-color:var(--red-deep);color:var(--red-soft)}
  button.cr-mini.yes:hover{background:#4a1a1a;border-color:var(--red)}
  .cr-confirm{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
  .cr-confirm .q{font-size:12px;color:var(--warn);white-space:normal;text-align:right}
  button.cr-danger{background:#3a1414;border-color:var(--red-deep);color:var(--red-soft)}
  button.cr-danger:hover{background:#4a1a1a;border-color:var(--red)}
  .cr-lock{color:var(--dim);font-size:12px}
  table.cr-bets .cr-tag.mob{display:none}   /* the Status column's OFF, folded into the label on phones */

  /* place a bet */
  .cr-pb{grid-template-columns:repeat(2,minmax(0,1fr))}
  .cr-limits{font-size:12px;color:var(--dim);margin-top:8px}
  .bres .cr-seq{font:12px var(--mono);color:var(--dim)}

  /* ledger */
  .cr-lsum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:2px 0 12px}
  .cr-tile{background:#0c0c11;border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:0}
  .cr-tile .lbl{display:block;font-size:10px;color:var(--dim);letter-spacing:.1em;text-transform:uppercase;font-weight:600}
  .cr-tile b{display:block;font:800 19px/1.3 var(--mono);color:var(--ink);font-variant-numeric:tabular-nums;
     white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .cr-tile small{display:block;font-size:11px;color:var(--dim)}
  .cr-pos{color:var(--good) !important} .cr-neg{color:var(--red-soft) !important}
  .cr-lbar{display:flex;gap:9px;flex-wrap:wrap;align-items:flex-end}
  .cr-lbar label.f{flex:1 1 200px}
  table.cr-grid{width:100%;border-collapse:collapse;font-size:12.5px}
  table.cr-grid th{position:sticky;top:0;z-index:1;background:var(--panel);text-align:left;font:600 10.5px var(--mono);
     letter-spacing:.08em;text-transform:uppercase;color:var(--dim);padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
  table.cr-grid td{padding:5px 8px;border-top:1px solid var(--line);white-space:nowrap;color:var(--ink)}
  table.cr-grid td.num,table.cr-grid th.num{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}
  table.cr-grid td.dim{color:var(--dim)}
  table.cr-grid td.wrap{white-space:normal;overflow-wrap:anywhere;min-width:90px}
  table.cr-grid td.mono{font-family:var(--mono);font-size:12px}
  table.cr-grid tr.fresh td{animation:cr-flash 1.8s ease-out}
  @keyframes cr-flash{from{background:#2c2610}to{background:transparent}}
  .cr-dc{font:800 10px/1 var(--mono);letter-spacing:.08em;padding:3px 6px;border-radius:99px}
  .cr-dc.debit{color:var(--red-soft);border:1px solid var(--red-deep);background:#2a1414}
  .cr-dc.credit{color:var(--good);border:1px solid #1d4d33;background:#0e2118}
  .cr-scroll{max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin-top:10px}
  .cr-scroll.short{max-height:220px}
  .cr-lnote{font-size:12px;color:var(--dim);margin-top:8px}

  /* Hex display (also uses the page's .dlines / .dnow / pre.dio) */
  .cr-dgrid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:18px}
  .cr-dgrid > div{min-width:0}
  .cr-dsp{grid-template-columns:repeat(2,minmax(0,1fr))}
  .cr-dgrid .dl{margin-top:12px}

  /* api */
  ol.cr-steps{margin:6px 0 4px;padding-left:22px;font-size:13px;color:var(--dim)}
  ol.cr-steps li{margin:0 0 7px}
  ol.cr-steps b{color:var(--ink)}

  @media (max-width: 860px){
    .cr-hgrid{grid-template-columns:1fr}
    .cr-lsum{grid-template-columns:repeat(2,minmax(0,1fr))}
    .cr-dgrid{grid-template-columns:1fr}
  }
  @media (max-width: 560px){
    .cr-facts{grid-template-columns:repeat(2,minmax(0,1fr))}
    .cr-pb{grid-template-columns:1fr}
    .cr-dsp{grid-template-columns:1fr}
    table.cr-grid .opt,table.cr-bets .opt{display:none}
    table.cr-bets .cr-tag.mob{display:inline-block}
    .cr-stats{grid-template-columns:1fr 1fr}
  }`;

  // ======================================================================
  // markup
  // ======================================================================
  function settingsGrid() {
    return `
      <div class="grid">
        <label class="f">Roll seconds (2.5–10)<input type="number" id="cr-s-roll_seconds" min="2.5" max="10" step="0.5"></label>
        <label class="f">Result seconds (1–120)<input type="number" id="cr-s-result_seconds" min="1" max="120" step="0.5"></label>
        <label class="f">Cooldown seconds (0–3600)<input type="number" id="cr-s-cooldown_seconds" min="0" max="3600" step="1"></label>
        <label class="f">Bet window seconds (5–300)<input type="number" id="cr-s-bet_window_seconds" min="5" max="300" step="1"></label>
        <label class="f">Currency name<input id="cr-s-currency" maxlength="24" placeholder="hexcoins" autocomplete="off" spellcheck="false"></label>
        <label class="f">Min bet (whole coins)<input type="number" id="cr-s-min_bet" min="1" max="1000000000" step="1"></label>
        <label class="f">Max bet (0 = no max)<input type="number" id="cr-s-max_bet" min="0" max="1000000000000" step="1"></label>
        <label class="f">Odds limit<select id="cr-s-odds_rule" title="3-4-5×: odds up to 3× the flat bet on 4/10, 4× on 5/9, 5× on 6/8; lay odds up to 6×">${ODDS_RULES.map(function (o) { return '<option value="' + o[0] + '">' + esc(o[1]) + '</option>'; }).join('')}</select></label>
        <label class="f">Field 12 pays<select id="cr-s-field_12_pays"><option value="3">3 to 1 (2.78% edge)</option><option value="2">2 to 1 (5.56% edge)</option></select></label>
        <label class="f">Throw clip<input id="cr-s-roll_clip" list="clip-list" placeholder="— none —" autocomplete="off" spellcheck="false"></label>
        <label class="f">Landing clip<input id="cr-s-land_clip" list="clip-list" placeholder="— none —" autocomplete="off" spellcheck="false"></label>
        <label class="f">Built-in sound volume
          <span class="rng"><input type="range" id="cr-s-sfx_volume" min="0" max="1" step="0.05"><b id="cr-s-sfx_volume-val">50%</b></span>
        </label>
      </div>
      <div class="grid" style="margin-top:14px">
        <label class="f sw"><input type="checkbox" id="cr-s-hide_when_idle"> Hide the table when idle</label>
        <label class="f sw"><input type="checkbox" id="cr-s-show_when_bets"> Keep it on screen while bets are down</label>
        <label class="f sw"><input type="checkbox" id="cr-s-auto_roll"> Auto-roll after the bet window</label>
        <label class="f sw"><input type="checkbox" id="cr-s-sfx"> Built-in dice sounds</label>
      </div>`;
  }

  function buildMarkup() {
    HOST.innerHTML = `
  <div class="cr-banner" id="cr-nobackend"><b>Craps isn't running on this Hexcast yet.</b> The page is new but the server
    is still the old one — restart Hexcast to load the craps table, bets and ledger.</div>

  <div class="card">
    <h2>OBS browser source</h2>
    <p class="hint">Add as a Browser source in OBS (1920×1080). The first URL shows every game in one source; the second shows only the craps table, so it can sit on its own layer. It stays transparent until the dice roll — or while a countdown runs, or bets are on the table if <b>Keep it on screen while bets are down</b> is on.</p>
    <div class="url"><a id="cr-u-all" target="_blank" rel="noopener"></a><button class="sec" id="cr-copy-all">Copy</button></div>
    <div class="url"><a id="cr-u-game" target="_blank" rel="noopener"></a><button class="sec" id="cr-copy-game">Copy</button></div>
  </div>

  <div class="card" id="cr-card-table">
    <h2>Craps <span class="pill" id="cr-ws-pill">connecting…</span></h2>
    <p class="hint">Live mirror of the overlay — the same throws landing on the same faces. The server throws two fair dice with a cryptographic RNG; nothing on this page (or the API) can set them. Turn on <b>Edit Mode</b> and click this card to place and style the table.</p>
    <div class="game">
      <div class="mirror" id="cr-mirror">
        <div class="gstage" id="cr-mirror-stage"><div class="gwheel cr-box off" id="cr-mirror-box"></div></div>
        <span class="mtag" id="cr-mirror-tag">hidden</span>
        <div class="pmsg" id="cr-mirror-msg" style="display:none"></div>
      </div>
      <div class="gside">
        <div class="kv">State</div>
        <div class="readout"><span class="cr-readout" id="cr-readout">—</span><span class="cr-state" id="cr-state">offline</span></div>
        <div class="cr-sub" id="cr-sub"></div>
        <div class="cr-facts">
          <div><span class="lbl">Phase</span><b id="cr-f-phase">—</b></div>
          <div><span class="lbl">Shooter</span><b id="cr-f-shooter">—</b></div>
          <div><span class="lbl">Hand</span><b id="cr-f-hand">—</b></div>
          <div><span class="lbl">On the table</span><b id="cr-f-total">—</b></div>
          <div><span class="lbl">Auto-roll</span><b id="cr-f-auto">—</b></div>
          <div><span class="lbl">Bets</span><b id="cr-f-open">—</b></div>
        </div>
        <div class="kv" style="margin-top:14px">Last roll</div>
        <div class="cr-last" id="cr-last"><span class="none">No rolls yet.</span></div>
        <div class="row">
          <input id="cr-shooter" maxlength="40" placeholder="shooter (optional)" autocomplete="off" spellcheck="false" title="Who throws — becomes the shooter if there is none">
          <button class="act" id="cr-roll">Roll dice</button>
        </div>
        <div class="row" style="margin-top:9px">
          <button class="sec" id="cr-show" title="Show the idle table on stream">Show</button>
          <button class="sec" id="cr-hide" title="Hide the table (not possible while the dice are in the air)">Hide</button>
          <button class="sec" id="cr-timer" title="Start the bet-window countdown now, with or without bets: the dice go by themselves at 0">Start timer</button>
          <button class="sec" id="cr-timer-cancel" title="Stop the countdown (auto-roll or started here); nothing is thrown">Cancel timer</button>
          <button class="sec edit-only" id="cr-edit-place">✎ Edit placement</button>
        </div>
      </div>
    </div>
  </div>

  <div class="two">
    <div class="card" id="cr-card-bets">
      <h2>On the table <span class="pill" id="cr-t-pill">no bets</span></h2>
      <p class="hint">Every bet riding right now. Contract bets — a pass line once a point is set, a come bet once it travels — can't come down; everything else can, and a take-down refunds the stake (a <code>credit</code> in the ledger). Bets are frozen while the dice are in the air.</p>
      <div class="tscroll"><table class="cr-bets" id="cr-bets"></table></div>
      <div class="row"><span class="note" id="cr-t-total"></span><span style="flex:1"></span><span id="cr-clear-wrap"></span></div>
    </div>

    <div class="card" id="cr-card-place">
      <h2>Place a bet <span class="cr-tag real">real coins</span></h2>
      <p class="hint"><b>Check</b> is a dry run against the table as it is now. <b>Place</b> is real: the bet goes on the table and a <b>debit</b> goes in the ledger, so Hex's ledger tail takes those coins from that user — and pays any win as a ledger <b>credit</b>. Test with test names. Pick a bet under <b>Odds on</b> to put odds behind exactly that bet (the bet text can stay empty).</p>
      <div class="grid cr-pb">
        <label class="f">User <input id="cr-b-user" maxlength="40" placeholder="viewer name" autocomplete="off" spellcheck="false"></label>
        <label class="f">Amount <input type="number" id="cr-b-amount" min="1" step="1" placeholder="whole coins"></label>
        <label class="f">Bet <input id="cr-b-bet" placeholder="pass, place6, hard8, field, odds…" autocomplete="off" spellcheck="false"></label>
        <label class="f">Odds on <select id="cr-b-target"><option value="">auto — from the bet text</option></select></label>
      </div>
      <div class="ex" id="cr-b-examples"></div>
      <div class="cr-limits" id="cr-b-limits"></div>
      <div class="bout" id="cr-b-out"><span class="none">Type a bet (or pick an example) to check it against the table.</span></div>
      <div class="row">
        <button class="sec" id="cr-b-check">Check</button>
        <button class="act" id="cr-b-place">Place bet</button>
      </div>
      <div id="cr-b-result"></div>
    </div>
  </div>

  <div class="card" id="cr-card-display">
    <h2>Hex display <span class="pill" id="cr-d-pill">nothing up</span></h2>
    <p class="hint">For when <b>Hex does the math</b>: Hex keeps the bets and pays from its own hexbank; Hexcast still throws the dice and shows what Hex says. <b>Set board</b> shows Hex's bets as the "on the table" board, in place of this table's own; <b>Announce</b> puts Hex's payouts card up after a roll, in place of the computed one (posted while the dice fly, it waits for the landing; a new roll clears it). One line each — <code>user amount text…</code>, e.g. <code>alice 150 Pass 100 + odds 50</code>: the amount is optional (never negative on the board), <code>-</code> as the user means none; an empty box sends a card with just the empty text / an empty board. Nothing here touches this table's bets, the ledger, history or stats; the mirror above shows it all.</p>
    <div class="cr-dgrid">
      <div>
        <div class="kv">Payouts card — announce</div>
        <div class="grid cr-dsp">
          <label class="f">Title<input id="cr-d-title" maxlength="40" placeholder="WINNERS" autocomplete="off" spellcheck="false"></label>
          <label class="f">Empty text<input id="cr-d-empty" maxlength="60" placeholder="No winners" autocomplete="off"></label>
          <label class="f">Seconds (1–120)<input type="number" id="cr-d-seconds" min="1" max="120" step="1" placeholder="auto"></label>
          <label class="f">Roll id<input id="cr-d-spin" maxlength="40" placeholder="latest roll" autocomplete="off" spellcheck="false" title="Optional: only put the card up if this is the roll on screen (or the last one) — otherwise HTTP 409 stale"></label>
        </div>
        <label class="f dl">Lines — user amount text…<textarea id="cr-d-lines" class="dlines" rows="4" spellcheck="false" placeholder="alice 200 Pass line&#10;@bob -50 Field&#10;carol 70 Place 6"></textarea></label>
        <div class="row">
          <button class="act" id="cr-d-announce">Announce</button>
          <button class="sec" id="cr-d-clear" title="Take Hex's payouts card down now">Clear</button>
          <span class="note" id="cr-d-count"></span>
        </div>
      </div>
      <div>
        <div class="kv">On the table — board</div>
        <div class="grid cr-dsp">
          <label class="f">Board title<input id="cr-d-btitle" maxlength="40" placeholder="ON THE TABLE" autocomplete="off" spellcheck="false"></label>
        </div>
        <label class="f dl">Bets — user amount text…<textarea id="cr-d-board" class="dlines" rows="4" spellcheck="false" placeholder="alice 150 Pass 100 + odds 50&#10;bob 25 Field"></textarea></label>
        <div class="row">
          <button class="act" id="cr-d-setboard">Set board</button>
          <button class="sec" id="cr-d-clearboard" title="Back to the board computed from this table's own bets">Clear board</button>
          <span class="note" id="cr-d-bcount"></span>
        </div>
      </div>
    </div>
    <div class="dnow" id="cr-d-now"></div>
    <div class="bout" id="cr-d-out"><span class="none">The request and the server's reply show here.</span></div>
  </div>

  <div class="card" id="cr-card-ledger">
    <h2>Ledger <span class="pill" id="cr-l-pill">loading…</span></h2>
    <p class="hint">Every coin movement, in order. A <b>debit</b> means the bank takes coins from the user, a <b>credit</b> means it pays them. Each event has a strictly increasing <code>seq</code>: the bank applies each one exactly once. New events stream in live.</p>
    <div class="cr-lsum" id="cr-l-sum"></div>
    <div class="cr-lbar">
      <label class="f">Filter by user <input id="cr-l-user" list="cr-l-users" placeholder="everyone" autocomplete="off" spellcheck="false"></label>
      <button class="sec" id="cr-l-all">Everyone</button>
      <button class="sec" id="cr-l-reload" title="Re-read the ledger from the server">Reload</button>
    </div>
    <datalist id="cr-l-users"></datalist>
    <div class="cr-scroll short"><table class="cr-grid" id="cr-l-nets"></table></div>
    <div class="cr-scroll"><table class="cr-grid" id="cr-l-table"></table></div>
    <div class="cr-lnote" id="cr-l-note"></div>
  </div>

  <div class="card" id="cr-card-history">
    <h2>History &amp; stats</h2>
    <p class="hint">Last 20 rolls, newest first (sevens in red, winners in green, points in gold). Stats cover every roll the server remembers since the last clear or restart — test rolls never count.</p>
    <div class="cr-hist" id="cr-h-rolls"></div>
    <div class="cr-hgrid">
      <div>
        <div class="kv">Totals vs fair dice</div>
        <div class="cr-histo" id="cr-h-histo"></div>
        <div class="cr-hx">${[2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(function (t) { return '<span>' + t + '</span>'; }).join('')}</div>
        <div class="cr-legend"><span><i class="b"></i>seen</span><span><i class="e"></i>expected with fair dice</span><span id="cr-h-n"></span></div>
      </div>
      <div>
        <div class="kv">The game</div>
        <div class="cr-stats" id="cr-h-stats"></div>
      </div>
    </div>
    <div class="row"><span class="note" id="cr-h-count"></span><span style="flex:1"></span><button class="sec" id="cr-h-clear">Clear history</button></div>
  </div>

  <div class="card" id="cr-card-settings">
    <h2>Settings</h2>
    <p class="hint">Timing, money rules and sounds for the craps table. Placement and look live in <b>Edit Mode</b> (click the Craps card). Soundboard clips fire server-side on the soundboard overlay when the dice are thrown / land — keep that browser source in the scene too.</p>
    ${settingsGrid()}
    <div class="row">
      <button class="act" id="cr-s-save">Save settings</button>
      <button class="sec" id="cr-s-revert">Revert</button>
      <span class="dirty" id="cr-s-dirty"></span>
    </div>
  </div>

  <div class="card" id="cr-card-api">
    <h2>API</h2>
    <p class="hint">Plain HTTP, every endpoint returns <code>{"ok": true|false, …}</code>. A roll while the dice are in the air, the result is on screen or the table is cooling down gets HTTP 409 <code>{"error":"busy","retry_in_ms":…}</code>; bets and take-downs while the dice fly get 409 <code>{"error":"bets_closed","retry_in_ms":…}</code>. Full reference as JSON at <code>GET /games/api</code>; the long version is in <code>docs/craps.md</code>.</p>
    <div class="tscroll"><table class="api" id="cr-api-table"></table></div>
    <h3>Bet syntax (case-insensitive; spaces, underscores, hyphens and apostrophes ignored; whole coins)</h3>
    <div class="tscroll"><table class="api" id="cr-bet-table"></table></div>
    <h3>Examples</h3>
    <pre id="cr-api-curl"></pre>
    <h3>Hooking up the bank (hexcoins)</h3>
    <ol class="cr-steps" id="cr-bank"></ol>
  </div>`;
  }

  // ======================================================================
  // config
  // ======================================================================
  var CFG_RAW = null;
  function cfgFull() { return Object.assign({}, DEFAULTS, CFG_RAW || {}); }
  function cur() { var c = (TABLE && TABLE.currency) || cfgFull().currency; return String(c || 'hexcoins'); }
  function diceStyle() { var d = cfgFull().dice_style; return /^(red|white|black|gold)$/.test(d) ? d : 'red'; }
  var BACKEND = null;   // true / false once known
  var _ledgerBooted = false;
  function noBackend(on) {
    BACKEND = !on;
    var b = $('cr-nobackend'); if (b) b.classList.toggle('on', !!on);
    if (!on) bootLedger();   // the server has craps: read the ledger (once; the socket keeps it live)
    else if (LSTATUS === 'loading' && !LEDGER.length) { LSTATUS = 'unavailable'; renderLedgerSoon(); }
  }
  function bootLedger() {
    if (_ledgerBooted) return;
    _ledgerBooted = true;
    fetchLedger(0);
  }
  function onConfig(config) {
    if (!isObj(config)) return;
    var cc = config.craps;
    if (!isObj(cc)) { noBackend(true); return; }
    noBackend(false);
    CFG_RAW = cc;
    fillSettings(false);
    renderLimits();
    if (ensureMirror()) mirrorConfig();
    if (ED) ED.refresh();
    renderTableAll(true);
    renderLast();
    renderRolls();
  }

  // ======================================================================
  // stage views (a 1920x1080 stage scaled into a 16:9 box; the tray is BASE_W x BASE_H)
  // ======================================================================
  function makeView(box, stage, el) {
    var v = { box: box, stage: stage, el: el, inst: null, _rz: false, ro: null };
    el.style.width = baseW() + 'px'; el.style.height = baseH() + 'px';
    fitView(v);
    if (window.ResizeObserver) { v.ro = new ResizeObserver(function () { fitView(v); }); v.ro.observe(box); }
    return v;
  }
  function fitView(v) {
    var w = v.box.clientWidth;
    if (!w) return;
    v.stage.style.transform = 'scale(' + (w / 1920) + ')';
    requestResize(v);
  }
  function placeView(v, x, y, scale) {
    v.el.style.left = x + '%';
    v.el.style.top = y + '%';
    v.el.style.transform = 'translate(-50%,-50%) scale(' + scale + ')';
    requestResize(v);
  }
  function requestResize(v) {
    if (v._rz) return;
    v._rz = true;
    requestAnimationFrame(function () { v._rz = false; if (v.inst) safe(function () { v.inst.resize(); }); });
  }
  function viewMsg(el, text) { el.textContent = text || ''; el.style.display = text ? 'flex' : 'none'; }
  // The editor closes on a click on its backdrop only when the press started there too:
  // selecting text in an input and letting go outside the modal is not a click outside.
  function closeOnBackdrop(ov, close) {
    var downOnOv = false;
    ov.addEventListener('pointerdown', function (e) { downOnOv = e.target === ov; });
    ov.addEventListener('click', function (e) { var d = downOnOv; downOnOv = false; if (e.target === ov && d) close(); });
  }

  // ======================================================================
  // live mirror (driven by the WS state exactly like the overlay's reconcile)
  // ======================================================================
  var STATE = null, STATE_AT = 0;
  var TABLE = null, TABLE_AT = 0;
  var MIR = null;
  var MIRS = { id: null, cfgKey: '', hist: null, table: null, ann: null, idled: false, failed: false };
  function ensureMirror() {
    if (MIR && MIR.inst) return true;
    if (!MIR || MIRS.failed) return false;
    var c = C();
    if (!c || typeof c.create !== 'function') {
      MIRS.failed = true;
      MIR.el.classList.add('empty');
      viewMsg($('cr-mirror-msg'), 'Craps renderer not loaded (' + ((typeof GP.gameScript === 'function' && GP.gameScript('craps')) || 'craps.js') + ').');
      return false;
    }
    var eff = mirrorEffective();
    try { MIR.inst = c.create(MIR.el, eff, { sound: false }); }
    catch (e) {
      console.error('[craps] renderer create failed:', e);
      MIRS.failed = true;
      MIR.el.classList.add('empty');
      viewMsg($('cr-mirror-msg'), 'Craps renderer failed to start: ' + (e && e.message || e));
      return false;
    }
    MIRS.cfgKey = JSON.stringify(eff);
    placeView(MIR, num(eff.x, 50), num(eff.y, 50), num(eff.scale, 1));
    return true;
  }
  // Saved config, plus the current roll's appearance overrides while it is on screen.
  function mirrorEffective() {
    var c = cfgFull();
    var sp = STATE && STATE.spin;
    if (sp && isObj(sp.overrides)) {
      APPEARANCE.forEach(function (k) { if (sp.overrides[k] != null) c[k] = sp.overrides[k]; });
    }
    return c;
  }
  function mirrorConfig() {
    if (!MIR || !MIR.inst) return;
    var eff = mirrorEffective();
    var key = JSON.stringify(eff);
    if (key !== MIRS.cfgKey) { MIRS.cfgKey = key; safe(function () { MIR.inst.setConfig(eff); }); }
    placeView(MIR, num(eff.x, 50), num(eff.y, 50), num(eff.scale, 1));
  }
  function setMirrorVisible(on) {
    if (!MIR) return;
    MIR.el.classList.toggle('off', !on);
    var t = $('cr-mirror-tag');
    t.textContent = on ? 'on stream' : 'hidden';
    t.classList.toggle('on', !!on);
  }
  // The table as the renderer should see it now (auto-roll countdown aged since it arrived).
  function liveTable() {
    if (!isObj(TABLE)) return null;
    var t = Object.assign({}, TABLE);
    if (typeof t.auto_roll_in_ms === 'number' && isFinite(t.auto_roll_in_ms)) {
      t.auto_roll_in_ms = Math.max(0, Math.round(t.auto_roll_in_ms - (performance.now() - TABLE_AT)));
    }
    return t;
  }
  function mirrorTable(force) {
    if (!MIR || !MIR.inst || typeof MIR.inst.setTable !== 'function' || !isObj(TABLE)) return;
    var key = JSON.stringify(TABLE);
    if (!force && key === MIRS.table) return;
    MIRS.table = key;
    var t = liveTable();
    safe(function () { MIR.inst.setTable(t); });
  }
  function histKey(h) { return (h || []).map(function (r) { return r && (r.label || r.number); }).join(','); }
  function mirrorState(st) {
    setMirrorVisible(!!st.visible);
    if (!ensureMirror()) return;
    mirrorConfig();
    var inst = MIR.inst, sp = st.spin;
    mirrorTable(false);                         // table BEFORE play (renderer holds it while dice fly)
    var hk = histKey(st.history) + '|' + ((st.last && st.last.id) || '');
    if (hk !== MIRS.hist && typeof inst.setHistory === 'function') { MIRS.hist = hk; safe(function () { inst.setHistory(st.history || []); }); }
    var acted = false;                          // play / showResult / reset: each clears Hex's card
    if (sp && sp.id) {
      if (sp.id !== MIRS.id) {
        MIRS.id = sp.id; MIRS.idled = false; acted = true;
        var landed = !rolling(st.state) || (+sp.elapsed_ms || 0) >= (+sp.duration_ms || 0);
        if (landed) safe(function () { inst.showResult(sp); });
        else safe(function () { var p = inst.play(sp); if (p && typeof p.catch === 'function') p.catch(noop); });
      }
    } else if (MIRS.id !== null || !MIRS.idled) {
      MIRS.id = null; MIRS.idled = true; acted = true;
      safe(function () { inst.reset(); });
      mirrorTable(true);                        // whatever reset() cleared, the table is still there
    }
    mirrorAnnounce(inst, st.announce, acted);   // AFTER play (held while the dice fly), like the overlay
  }
  // Hex's payouts card (STATE.announce): sent when it changed (expires_in_ms ticks don't
  // count), and again after play/showResult/reset cleared it. Optional renderer method.
  function annKey(a) {
    if (!isObj(a)) return null;
    var o = {};
    Object.keys(a).sort().forEach(function (k) { if (k !== 'expires_in_ms') o[k] = a[k]; });
    return JSON.stringify(o);
  }
  function mirrorAnnounce(inst, a, acted) {
    var k = annKey(a);
    if (k === MIRS.ann && !(k && acted)) return;
    MIRS.ann = k;
    if (typeof inst.setAnnounce === 'function') safe(function () { inst.setAnnounce(k ? a : null); });
  }
  function onStop(game) {
    if (game && game !== 'craps') return;
    setMirrorVisible(false);
    MIRS.id = null; MIRS.idled = true; MIRS.ann = null;
    if (MIR && MIR.inst) { safe(function () { MIR.inst.reset(); }); mirrorTable(true); }
  }

  // ======================================================================
  // state readout + facts + last roll
  // ======================================================================
  function autoLeft() {
    if (!TABLE || typeof TABLE.auto_roll_in_ms !== 'number' || !isFinite(TABLE.auto_roll_in_ms)) return null;
    return Math.max(0, TABLE.auto_roll_in_ms - (performance.now() - TABLE_AT));
  }
  function renderReadout() {
    var elR = $('cr-readout'), elS = $('cr-state'), elSub = $('cr-sub');
    var st = STATE;
    if (!st) {
      elR.textContent = '—';
      elS.textContent = BACKEND === false ? 'not loaded' : 'offline';
      elS.className = 'cr-state'; elSub.textContent = ''; return;
    }
    var dt = performance.now() - STATE_AT;
    var s = stateName(st.state), sp = st.spin;
    var busy = Math.max(0, (+st.busy_ms || 0) - dt);
    var phase = null, sub = '', label = s;
    if (s === 'rolling' && sp) {
      phase = (+sp.duration_ms || 0) - (+sp.elapsed_ms || 0) - dt;
      sub = 'until the dice land' + (busy > 0 ? ' · next roll in ' + fmtSec(busy) : '');
    } else if (s === 'result' && sp) {
      var rm = (sp.result_ms != null) ? +sp.result_ms : num(cfgFull().result_seconds, 5) * 1000;
      phase = (+sp.duration_ms || 0) + rm - (+sp.elapsed_ms || 0) - dt;
      sub = 'result on screen' + (busy > 0 ? ' · next roll in ' + fmtSec(busy) : '');
    } else if (s === 'result' || s === 'cooldown') {
      phase = busy;
      sub = s === 'cooldown' ? 'cooling down — next roll when this hits 0' : 'result on screen';
    } else {
      var left = autoLeft();
      if (left != null) { phase = left; label = 'betting'; sub = 'bet window — the dice go by themselves when this hits 0'; }
      else sub = st.visible ? 'ready — the table is showing' : 'ready to roll';
    }
    elR.textContent = (phase == null) ? '—' : (label === 'betting' ? fmtClock(phase) : fmtSec(phase));
    elS.textContent = label;
    elS.className = 'cr-state ' + label;
    elSub.textContent = sub;
  }
  function fact(id, html, cls, title) {
    var el = $(id); if (!el) return;
    el.innerHTML = html;
    el.className = cls || '';
    el.title = title || '';
  }
  function renderFacts() {
    var t = TABLE;
    if (!t) {
      ['cr-f-phase', 'cr-f-shooter', 'cr-f-hand', 'cr-f-total', 'cr-f-open'].forEach(function (id) { fact(id, '—', 'dim'); });
      renderAuto();
      return;
    }
    var point = t.phase === 'point' && t.point != null;
    fact('cr-f-phase', point
      ? '<span class="cr-puck on">ON</span><span class="t">POINT ' + esc(t.point) + '</span>'
      : '<span class="cr-puck">OFF</span><span class="t">COME-OUT</span>', '',
      point ? 'The point is ' + t.point + ' — pass wins if it rolls before a 7' : 'Come-out roll: 7/11 win the pass line, 2/3/12 lose, anything else sets the point');
    fact('cr-f-shooter', t.shooter ? '<span class="t">@' + esc(t.shooter) + '</span>' : 'none', t.shooter ? '' : 'dim',
      t.shooter ? '@' + t.shooter + ' has the dice' : 'The next roll\'s user becomes the shooter');
    fact('cr-f-hand', esc(+t.hand_rolls || 0) + ' roll' + (+t.hand_rolls === 1 ? '' : 's'), '', 'Rolls in this shooter\'s hand');
    var n = (t.bets || []).length;
    fact('cr-f-total', '<span class="t">' + esc(fmtN(t.total_on_table)) + '</span>', n ? '' : 'dim',
      plural(n, 'bet') + ' · ' + coins(t.total_on_table));
    fact('cr-f-open', t.bets_open === false ? 'closed' : 'open', t.bets_open === false ? 'warn' : 'good',
      t.bets_open === false ? 'Dice in the air — bets and take-downs wait for the landing' : 'Bets and take-downs are accepted');
    renderAuto();
  }
  function renderAuto() {
    var left = autoLeft(), c = cfgFull();
    if (left != null) fact('cr-f-auto', 'in ' + fmtClock(left), 'good', 'Countdown — auto-roll or Start timer: the dice go by themselves at 0');
    else if (c.auto_roll) fact('cr-f-auto', (TABLE && (TABLE.bets || []).length) ? 'armed' : 'waiting', 'dim',
      'Auto-roll is on: a ' + c.bet_window_seconds + 's countdown starts when bets are down and the table is idle');
    else fact('cr-f-auto', 'off', 'dim', 'Auto-roll is off — Start timer, or roll from here, a bot or chat');
  }
  // Start / cancel the countdown (/timer, /timer/cancel). A 404 here is a Hexcast from before
  // the timer — not one without craps, so no banner (unlike api()).
  async function timerReq(path, verb) {
    var res = await req('POST', path);
    if (res.net) { toast('Network error — is Hexcast running?', 2400); return null; }
    if (isBusy(res)) { toast(busyText(res.d, verb), 3000); return null; }
    if (res.status === 404 || res.status === 405) { toast('This Hexcast has no timer yet — restart it after updating', 2600); return null; }
    if (!res.ok) { toast(errText(res), 2600); return null; }
    if (isObj(res.d.table)) applyTable(res.d.table);
    return res.d;
  }
  async function startTimer() {
    var d = await timerReq(API + '/timer', "Can't start the timer");
    if (!d) return;
    var ms = typeof d.auto_in_ms === 'number' ? d.auto_in_ms : autoLeft();
    toast('Timer started — the dice go' + (ms != null ? ' in ' + fmtClock(ms) : ' when it runs out'), 2400);
  }
  async function cancelTimer() {
    var d = await timerReq(API + '/timer/cancel', "Can't cancel the timer");
    if (d) toast(d.cancelled === false ? 'No timer was running' : 'Timer cancelled — nothing is thrown');
  }
  function eventClass(r) {
    if (!r) return '';
    var t = totalOf(r);
    if (r.event === 'seven_out' || (t === 7 && r.event !== 'natural')) return 'seven';
    if (r.event === 'natural' || r.event === 'point_made') return 'win';
    if (r.event === 'point_set') return 'point';
    return '';
  }
  function renderLast() {
    var host = $('cr-last'), st = STATE;
    var sp = null;
    if (st) {
      // The in-flight roll only once it has landed (never spoil it); otherwise the last committed one.
      if (st.spin && st.spin.result && st.state && !rolling(st.state) && st.state !== 'idle') sp = st.spin;
      else if (st.last && st.last.result) sp = st.last;
    }
    if (!sp) { host.innerHTML = '<span class="none">No rolls yet.</span>'; return; }
    var r = sp.result, d = diceOf(r);
    var who = [];
    var shooter = sp.shooter || sp.user;
    if (shooter) who.push('@' + esc(shooter));
    var sm = sp.summary;
    if (sm && +sm.bets_settled) {
      who.push(esc(plural(+sm.bets_settled, 'bet')) + ' settled · ' + esc(+sm.winners || 0) + ' won' +
        (+sm.total_credited ? ' · paid ' + esc(coins(sm.total_credited)) : ''));
    }
    host.innerHTML =
      '<span class="dd">' + dieHTML(d[0]) + dieHTML(d[1]) + '</span>' +
      '<span class="meta"><span class="call"><em>' + esc(totalOf(r)) + '</em>' + esc(r.call || '') +
      (sp.test ? '<span class="tagt">TEST</span>' : '') + '</span>' +
      (r.sub ? '<span class="who">' + esc(r.sub) + '</span>' : '') +
      (who.length ? '<span class="who">' + who.join(' · ') + '</span>' : '') + '</span>';
  }

  // ======================================================================
  // on the table (bets list, take-down, refund all)
  // ======================================================================
  var CONFIRM = null, _confirmT = null;   // {key, until}
  function askConfirm(key) {
    CONFIRM = { key: key };
    clearTimeout(_confirmT);
    _confirmT = setTimeout(function () { CONFIRM = null; renderTableAll(true); }, 8000);
    renderTableAll(true);
  }
  function cancelConfirm() { CONFIRM = null; clearTimeout(_confirmT); renderTableAll(true); }
  function confirming(key) { return CONFIRM && CONFIRM.key === key; }
  function confirmHTML(key, q, yes) {
    return '<span class="cr-confirm"><span class="q">' + q + '</span>' +
      '<button class="cr-mini yes" data-yes="' + esc(key) + '">' + esc(yes) + '</button>' +
      '<button class="cr-mini" data-no="1">Keep</button></span>';
  }
  function oddsSyntax(b) {
    if (b.type === 'pass') return 'odds:pass';
    if (b.type === 'dont_pass') return 'odds:dontpass';
    if (b.type === 'come' && b.number != null) return 'odds:come' + b.number;
    if (b.type === 'dont_come' && b.number != null) return 'odds:dontcome' + b.number;
    return null;
  }
  function betsByUser(bets) {
    var map = dict(), order = [];
    (bets || []).forEach(function (b) {
      if (!b) return;
      var u = String(b.user || '—');
      if (!map[u]) { map[u] = []; order.push(u); }
      map[u].push(b);
    });
    var exp = (TABLE && TABLE.exposure) || {};
    function expOf(u) { return own(exp, u) && exp[u] != null ? +exp[u] : map[u].reduce(function (a, b) { return a + (+b.amount || 0) + (+b.odds || 0); }, 0); }
    order.sort(function (a, b) { return expOf(b) - expOf(a) || a.localeCompare(b); });
    return order.map(function (u) { return { user: u, bets: map[u], exposure: expOf(u) }; });
  }
  var _tableKey = null;
  function renderTableAll(force) {
    var key = JSON.stringify([TABLE && TABLE.bets, TABLE && TABLE.bets_open, TABLE && TABLE.exposure, cur(), CONFIRM && CONFIRM.key]);
    if (!force && key === _tableKey) return;
    _tableKey = key;
    renderBets();
    renderTargets();
  }
  function renderBets() {
    var host = $('cr-bets'), t = TABLE, bets = (t && t.bets) || [];
    var open = !t || t.bets_open !== false;
    var dis = open ? '' : ' disabled title="Frozen while the dice are in the air"';
    $('cr-t-pill').textContent = bets.length ? plural(bets.length, 'bet') : 'no bets';
    $('cr-t-pill').className = 'pill' + (bets.length ? ' on' : '');
    if (!bets.length) {
      host.innerHTML = '<tbody><tr><td class="cr-empty">' + (t ? 'Nothing on the table. Bets placed by chat, a bot or the form next door show up here.' : 'Waiting for the table…') + '</td></tr></tbody>';
      $('cr-t-total').textContent = '';
      $('cr-clear-wrap').innerHTML = '';
      return;
    }
    var rows = ['<thead><tr><th>Bet</th><th class="num">Flat</th><th class="num">Odds</th><th class="opt">Status</th><th></th></tr></thead><tbody>'];
    betsByUser(bets).forEach(function (g) {
      // {user, all:true} takes down every removable bet AND the odds behind this user's contract bets
      var removable = g.bets.filter(function (b) { return b.removable; });
      var oddsOnly = g.bets.filter(function (b) { return !b.removable && +b.odds && oddsSyntax(b); });
      var nDown = removable.length + oddsOnly.length;
      var refundAll = removable.reduce(function (a, b) { return a + (+b.amount || 0) + (+b.odds || 0); }, 0) +
        oddsOnly.reduce(function (a, b) { return a + (+b.odds || 0); }, 0);
      var ukey = 'user:' + g.user;
      var uact = '';
      if (nDown >= 2) {
        uact = confirming(ukey)
          ? confirmHTML(ukey, 'Take down all ' + nDown + ' of @' + esc(g.user) + '\'s ' + (oddsOnly.length ? 'bets and odds' : 'bets') +
              ' that can come down — refund ' + esc(fmtN(refundAll)) + '?', 'Take down')
          : '<button class="cr-mini" data-user="' + esc(g.user) + '"' + dis + ' title="Every bet that can come down, plus the odds behind contract bets">Take down all</button>';
      }
      rows.push('<tr class="u"><td colspan="5"><div class="uh"><span><span class="who">@' + esc(g.user) + '</span><span class="n">' +
        esc(plural(g.bets.length, 'bet')) + ' · ' + esc(coins(g.exposure)) + ' on the table</span></span>' + uact + '</div></td></tr>');
      g.bets.forEach(function (b) {
        var tags = '';
        if (b.working === false) tags += '<span class="cr-tag tg off mob" title="Off — no action on a come-out roll">OFF</span>';
        if (b.one_roll) tags += '<span class="cr-tag tg" title="Resolves on the next roll">1 roll</span>';
        if (!b.removable) tags += '<span class="cr-tag tg" title="Contract bet — can\'t be taken down">contract</span>';
        var status = b.working === false
          ? '<span class="cr-tag off" title="Off — no action on a come-out roll">OFF</span>'
          : '<span class="cr-tag on" title="Working">ON</span>';
        var act = '', bkey = 'bet:' + b.id, okey = 'odds:' + b.id;
        var oddsTxt = LINE_TYPES[b.type] ? (+b.odds ? '+' + fmtN(b.odds) : '—') : '';
        var canOdds = +b.odds && oddsSyntax(b);
        if (confirming(okey) && canOdds) {
          act = confirmHTML(okey, 'Take down the ' + esc(fmtN(b.odds)) + ' odds (the flat bet stays)?', 'Take down odds');
        } else if (b.removable) {
          var refund = (+b.amount || 0) + (+b.odds || 0);
          act = confirming(bkey)
            ? confirmHTML(bkey, 'Refund ' + esc(fmtN(refund)) + ' to @' + esc(g.user) + '?', 'Take down')
            // a don't bet with lay odds: the odds can come down on their own too
            : '<span class="cr-acts">' +
              (canOdds ? '<button class="cr-mini" data-odds="' + esc(b.id) + '"' + dis + ' title="Take down only the odds; the flat bet stays">Odds down</button>' : '') +
              '<button class="cr-mini" data-bet="' + esc(b.id) + '"' + dis + '>Take down</button></span>';
        } else if (canOdds) {
          act = '<button class="cr-mini" data-odds="' + esc(b.id) + '"' + dis + ' title="The flat bet is a contract bet; the odds behind it can come down">Odds down</button>';
        } else {
          act = '<span class="cr-lock" title="Contract bet — it stays until it wins or loses">stays</span>';
        }
        rows.push('<tr><td class="bl" title="' + esc(b.id) + '">' + esc(b.label || b.type) + tags + '</td>' +
          '<td class="num">' + esc(fmtN(b.amount)) + '</td><td class="num">' + esc(oddsTxt) + '</td>' +
          '<td class="opt">' + status + '</td><td class="act">' + act + '</td></tr>');
      });
    });
    rows.push('</tbody>');
    host.innerHTML = rows.join('');
    var total = t.total_on_table != null ? +t.total_on_table : bets.reduce(function (a, b) { return a + (+b.amount || 0) + (+b.odds || 0); }, 0);
    var players = betsByUser(bets).length;
    $('cr-t-total').textContent = coins(total) + ' on the table · ' + plural(players, 'player');
    $('cr-clear-wrap').innerHTML = confirming('clear')
      ? confirmHTML('clear', 'Refund all ' + esc(coins(total)) + ' to ' + esc(plural(players, 'player')) + ' and reset to the come-out?', 'Refund all')
      : '<button class="sec cr-danger" id="cr-clear"' + dis + '>Refund all &amp; clear table</button>';
  }
  function findBet(id) { return ((TABLE && TABLE.bets) || []).filter(function (b) { return b && b.id === id; })[0] || null; }
  async function takeDown(key) {
    var i = key.indexOf(':'), kind = i < 0 ? key : key.slice(0, i), arg = i < 0 ? '' : key.slice(i + 1), body, what;
    if (kind === 'bet') {
      var b = findBet(arg);
      body = { bet_id: arg }; what = b ? (b.label || b.type) + (b.user ? ' (@' + b.user + ')' : '') : arg;
    } else if (kind === 'odds') {
      var ob = findBet(arg);
      if (!ob || !oddsSyntax(ob)) { cancelConfirm(); return; }
      body = { user: ob.user, bet: oddsSyntax(ob) }; what = 'odds on ' + (ob.label || ob.type) + ' (@' + ob.user + ')';
    } else if (kind === 'user') {
      body = { user: arg, all: true }; what = '@' + arg + '\'s bets';
    } else if (kind === 'clear') {
      return clearTable();
    } else return;
    CONFIRM = null; clearTimeout(_confirmT);
    renderTableAll(true);                     // drop the confirm now: a second click can't send it twice
    var d = await api('POST', API + '/remove', body, "Can't take down");
    renderTableAll(true);
    if (!d) return;
    var paid = (d.credits || []).reduce(function (a, c) { return a + (+c.amount || 0); }, 0);
    var n = (d.removed || []).length;
    toast('Took down ' + (n > 1 ? plural(n, 'bet') : what) + (paid ? ' — refunded ' + coins(paid) : ''), 2600);
    if (isObj(d.table)) applyTable(d.table);
  }
  async function clearTable() {
    CONFIRM = null; clearTimeout(_confirmT);
    renderTableAll(true);
    var d = await api('POST', API + '/clear', undefined, "Can't clear");
    renderTableAll(true);
    if (!d) return;
    var credits = d.credits || [];
    var paid = credits.reduce(function (a, c) { return a + (+c.amount || 0); }, 0);
    var users = dict(); credits.forEach(function (c) { users[c.user] = 1; });
    toast(paid ? 'Refunded ' + coins(paid) + ' to ' + plural(Object.keys(users).length, 'player') + ' — table cleared' : 'Table cleared', 3000);
    if (isObj(d.table)) applyTable(d.table);
  }
  function applyTable(t) {
    if (!isObj(t)) return;
    TABLE = t; TABLE_AT = performance.now();
    renderFacts();
    renderTableAll(false);
    renderLimits();
    renderLedgerSoon();
    statsFollowTable();
    renderDisplay();
  }

  // ======================================================================
  // place a bet (validate + real place)
  // ======================================================================
  var EXAMPLES = ['pass', 'dontpass', 'come', 'dontcome', 'odds', 'odds:dontpass', 'place6', 'place8', 'p5',
    'hard6', 'hard8', 'field', 'any7', 'anycraps', 'yo', 'aces', 'boxcars', 'horn', 'ce'];
  function renderExamples() {
    $('cr-b-examples').innerHTML = EXAMPLES.map(function (e) {
      return '<button type="button" data-bet="' + esc(e) + '">' + esc(e) + '</button>';
    }).join('');
  }
  function oddsRuleText(r) { return String(r) === '345' ? '3-4-5×' : String(r) + '×'; }
  function renderLimits() {
    var c = cfgFull(), t = TABLE || {};
    var min = t.min_bet != null ? t.min_bet : c.min_bet, max = t.max_bet != null ? t.max_bet : c.max_bet;
    var rule = t.odds_rule != null ? t.odds_rule : c.odds_rule;
    var f12 = t.field_12_pays != null ? t.field_12_pays : c.field_12_pays;
    $('cr-b-limits').textContent = 'Min ' + fmtN(min) + ' · max ' + (+max ? fmtN(max) : 'none') + ' · odds ' + oddsRuleText(rule) +
      ' · field 12 pays ' + (+f12 === 2 ? 2 : 3) + ' to 1 · winnings round down to whole ' + cur();
  }
  function renderTargets() {
    var sel = $('cr-b-target'), keep = sel.value;
    // exact name, like the server (it keeps case): a bet listed here is one this user can put odds behind
    var u = cleanUser($('cr-b-user').value);
    var opts = ['<option value="">auto — from the bet text</option>'];
    ((TABLE && TABLE.bets) || []).forEach(function (b) {
      if (!b || !LINE_TYPES[b.type]) return;
      if (u && String(b.user || '') !== u) return;
      if ((b.type === 'come' || b.type === 'dont_come') && b.number == null) return;   // still in the come box
      opts.push('<option value="' + esc(b.id) + '">' + (u ? '' : '@' + esc(b.user) + ' · ') + esc(b.label || b.type) +
        ' · ' + esc(fmtN(b.amount)) + (+b.odds ? ' +' + esc(fmtN(b.odds)) + ' odds' : '') + '</option>');
    });
    sel.innerHTML = opts.join('');
    sel.value = keep;
    if (sel.value !== keep) sel.value = '';
  }
  function betForm() {
    var o = { user: cleanUser($('cr-b-user').value), bet: $('cr-b-bet').value.trim() };
    var a = $('cr-b-amount').value.trim();
    if (a !== '') o.amount = Number(a);
    var tg = $('cr-b-target').value;
    if (tg) {
      // odds behind that exact bet: the server reads the target, not the text, and the bet's owner bets
      o.target = tg;
      if (!o.bet) o.bet = 'odds';
      if (!o.user) { var tb = findBet(tg); if (tb && tb.user) o.user = String(tb.user); }
    }
    return o;
  }
  var _vSeq = 0, _vT = null;
  function renderCheck(d) {
    var host = $('cr-b-out');
    if (!d) { host.innerHTML = '<span class="none">Type a bet (or pick an example) to check it against the table.</span>'; return; }
    if (d.valid === undefined && d.ok === false) { host.innerHTML = '<span class="bad">✕</span> ' + esc(d.error || 'could not check that bet'); return; }
    if (!d.valid) {
      host.innerHTML = '<span class="bad">✕ Not accepted</span> — ' + esc(d.error || 'not a recognised bet') +
        (d.hint ? '<div class="note" style="margin-top:4px">' + esc(d.hint) + '</div>' : '');
      return;
    }
    var bits = [];
    if (d.odds_text) bits.push('<span class="odds">' + esc(d.odds_text) + '</span>');
    if (d.max_odds != null) bits.push('max odds <b>' + esc(fmtN(d.max_odds)) + '</b>');
    if (d.action === 'add') bits.push('adds to the bet already down');
    host.innerHTML = '<span class="ok">✓ ' + esc(d.label || d.type) + '</span> <span class="note">(' + esc(d.type) +
      (d.number != null ? ' ' + esc(d.number) : '') + ')</span>' + (bits.length ? ' · ' + bits.join(' · ') : '') +
      (d.hint ? '<div class="note" style="margin-top:4px;color:var(--warn)">' + esc(d.hint) + '</div>' : '');
  }
  async function checkBet() {
    clearTimeout(_vT);
    var f = betForm(), seq = ++_vSeq;
    if (!f.bet) { renderCheck(null); return null; }
    var q = '?bet=' + encodeURIComponent(f.bet);
    if (f.user) q += '&user=' + encodeURIComponent(f.user);
    if (f.amount != null && isFinite(f.amount)) q += '&amount=' + encodeURIComponent(f.amount);
    if (f.target) q += '&target=' + encodeURIComponent(f.target);
    var res = await req('GET', API + '/validate' + q);
    if (seq !== _vSeq) return null;
    if (res.net) { renderCheck({ ok: false, error: 'check failed — is Hexcast running?' }); return null; }
    var d = res.d;
    if (!res.ok && d.valid === undefined) d = { ok: false, error: 'check failed — ' + errText(res) };
    renderCheck(d);
    return d;
  }
  async function placeBet() {
    var f = betForm();
    if (!f.user) { toast('Who is betting? Fill in the user'); $('cr-b-user').focus(); return; }
    if (!f.bet) { toast('Type a bet first'); $('cr-b-bet').focus(); return; }
    if (f.amount == null || !isFinite(f.amount) || f.amount < 1 || Math.floor(f.amount) !== f.amount) {
      toast('Amount must be a whole number of ' + cur()); $('cr-b-amount').focus(); return;
    }
    var body = { user: f.user, bet: f.bet, amount: f.amount };
    if (f.target) body.target = f.target;
    var btn = $('cr-b-place'); btn.disabled = true;
    var res = await req('POST', API + '/bet', body);
    btn.disabled = false;
    if (res.net) { toast('Network error — is Hexcast running?', 2400); return; }
    if (isBusy(res)) { toast(busyText(res.d, "Can't bet"), 3000); return; }
    var d = res.d;
    if (!Array.isArray(d.accepted) && !Array.isArray(d.rejected)) {
      if (!res.ok) toast(errText(res), 2600);
      return;
    }
    renderPlaced(d);
    if (isObj(d.table)) applyTable(d.table);
    var acc = (d.accepted || []).length;
    if (acc) {
      var deb = (d.debits || []).reduce(function (a, x) { return a + (+x.amount || 0); }, 0);
      toast('Bet placed — debit ' + coins(deb) + ' from @' + f.user, 2600);
    } else toast('Bet rejected — nothing was debited', 2600);
  }
  function renderPlaced(d) {
    var debits = dict();
    (d.debits || []).forEach(function (x) { if (x && x.bet_id) (debits[x.bet_id] = debits[x.bet_id] || []).push(x); });
    var rows = (d.accepted || []).map(function (b) {
      var ds = debits[b.id] || [];
      var deb = ds.map(function (x) { return '<span class="cr-seq">#' + esc(x.seq) + '</span> −' + esc(fmtN(x.amount)); }).join(', ');
      return '<div class="bres win"><b>ACCEPTED</b>' + esc(b.label || b.type) +
        ' <span class="u">@' + esc(b.user) + '</span> · <span class="m">' + esc(fmtN(b.amount)) +
        (+b.odds ? ' + ' + esc(fmtN(b.odds)) + ' odds' : '') + '</span>' +
        (deb ? ' · debit ' + deb : '') +
        (b.hint ? '<div class="note" style="margin:3px 0 0;color:var(--warn)">' + esc(b.hint) + '</div>' : '') + '</div>';
    });
    (d.rejected || []).forEach(function (r) {
      rows.push('<div class="bres lose"><b>REJECTED</b>' + esc(r.bet) + (r.amount != null ? ' · <span class="m">' + esc(r.amount) + '</span>' : '') +
        (r.user ? ' <span class="u">@' + esc(r.user) + '</span>' : '') + ' <span class="e">— ' + esc(r.error || 'rejected') + '</span></div>');
    });
    $('cr-b-result').innerHTML = rows.join('') || '<div class="note" style="margin-top:10px">Nothing was sent.</div>';
  }

  // ======================================================================
  // Hex display (DISPLAY_SPEC §1/§2/§5 — "Hex does the math"): display only
  // ======================================================================
  // Line parser, raw request, error text and exchange view come from the page
  // (window.GamesPage.display, shared with the roulette tab's card).
  var D = isObj(GP.display) ? GP.display : null;
  var ANNOUNCE_MAX = 50, BOARD_MAX = 100;
  function annLines() { return D.parseLines($('cr-d-lines').value); }
  function boardLines() { return D.parseLines($('cr-d-board').value, { signed: false }); }   // board amounts are >= 0
  function annBody(p) {
    var body = {};
    var t = $('cr-d-title').value.trim(); if (t) body.title = t;
    body.lines = p.lines.slice(0, ANNOUNCE_MAX);
    var e = $('cr-d-empty').value.trim(); if (e) body.empty_text = e;
    var s = parseFloat($('cr-d-seconds').value); if (isFinite(s)) body.seconds = s;
    var id = $('cr-d-spin').value.trim(); if (id) body.spin_id = id;
    return body;
  }
  function boardBody(p) {
    var body = {};
    var t = $('cr-d-btitle').value.trim(); if (t) body.title = t;
    body.bets = p.lines.slice(0, BOARD_MAX);
    return body;
  }
  function renderDCounts() {
    if (!D) return;
    var p = annLines(), b = boardLines();
    $('cr-d-count').textContent = (p.lines.length || p.skipped || p.bad) ? D.countText(p, ANNOUNCE_MAX) : '';
    $('cr-d-bcount').textContent = (b.lines.length || b.skipped || b.bad) ? D.countText(b, BOARD_MAX, 'bet') : '';
  }
  // Text in the box but not one usable line: say so instead of sending an empty card / board.
  function refused(p, what) {
    var no = typeof D.refuseText === 'function' ? D.refuseText(p, what) : null;
    if (!no) return false;
    $('cr-d-out').innerHTML = '<span class="bad">✕ ' + esc(no) + '</span>';
    toast('Nothing sent', 2400);
    return true;
  }
  // STATE.announce as it stands now (null once its countdown ran out)
  function liveAnn() {
    if (!STATE || !isObj(STATE.announce)) return null;
    var a = STATE.announce, left = D.left(a, STATE_AT);
    return left === 0 ? null : a;
  }
  function boardOf(t) { return isObj(t) && isObj(t.display_board) ? t.display_board : null; }
  function boardSummary(b) {
    var n = Array.isArray(b.bets) ? b.bets.length : 0;
    return '“' + (b.title || 'ON THE TABLE') + '” · ' + (n ? plural(n, 'line') : 'empty') +
      (b.total != null && +b.total ? ' · ' + coins(b.total) : '');
  }
  function renderDisplay() {
    var pill = $('cr-d-pill'), el = $('cr-d-now');
    if (!pill || !el) return;
    if (!D) { pill.textContent = 'unavailable'; pill.className = 'pill off'; return; }
    var a = liveAnn(), b = boardOf(TABLE);
    var held = !!(a && rolling(STATE.state) && STATE.spin && (!a.spin_id || a.spin_id === STATE.spin.id));
    var hidden = !!(STATE && !STATE.visible && !rolling(STATE.state));   // /hide, /stop, or hidden while idle
    var up = [];
    if (a) up.push(held ? 'card waiting' : 'card up');
    if (b) up.push('board up');
    if (up.length && hidden) up.push('hidden');
    pill.textContent = up.length ? up.join(' · ') : 'nothing up';
    pill.className = 'pill' + (up.length ? (hidden ? ' off' : ' on') : '');
    if (!STATE && !TABLE) { el.textContent = ''; return; }
    var n = TABLE && Array.isArray(TABLE.bets) ? TABLE.bets.length : 0;
    el.innerHTML = 'Payouts card: ' + (a ? '<b>' + esc(D.summary(a, D.left(a, STATE_AT))) + '</b>' + (held ? ' · waits for the dice to land' : '')
        : 'none — after a roll the table shows its own payouts') +
      '<br>Board: ' + (b ? '<b>Hex\'s ' + esc(boardSummary(b)) + '</b>'
        : 'this table\'s own bets (' + esc(n ? plural(n, 'bet') : 'none') + ')') +
      (up.length && hidden ? '<br>The table is hidden right now — <b>Show</b> puts it on stream.' : '');
  }
  async function dSend(btn, path, body, okText, after) {
    if (!D) return;
    btn.disabled = true;
    var res = await D.req(path, body);
    btn.disabled = false;
    var out = $('cr-d-out');
    if (res.ok) {
      D.out(out, true, okText(res.d), path, body, res);
      if (isObj(res.d.table)) applyTable(res.d.table);
      if (after) after();
    } else {
      D.out(out, false, D.error(res, 'roll'), path, body, res);
      toast(res.status === 409 && res.d.error === 'stale' ? 'Stale roll id' : "Can't update the display", 2400);
    }
    renderDisplay();
    return res;
  }
  function dAnnounce() {
    var p = annLines();
    if (refused(p, 'card')) return;
    return dSend($('cr-d-announce'), API + '/announce', annBody(p), function (d) {
      toast('Hex card up');
      return 'Card up — ' + (D.summary(d.announce, D.left(d.announce, performance.now())) || 'announced');
    });
  }
  function dClear() {
    return dSend($('cr-d-clear'), API + '/announce/clear', undefined, function () {
      toast('Hex card cleared');
      return 'Card cleared — the table is back to its own payouts';
    });
  }
  function dSetBoard() {
    var p = boardLines();
    if (refused(p, 'board')) return;
    return dSend($('cr-d-setboard'), API + '/board', boardBody(p), function (d) {
      var b = boardOf(d.table);
      toast('Board set');
      return 'Board set — ' + (b ? boardSummary(b) : 'showing Hex\'s bets');
    });
  }
  function dClearBoard() {
    return dSend($('cr-d-clearboard'), API + '/board/clear', undefined, function () {
      toast('Board cleared');
      return 'Board cleared — back to this table\'s own bets';
    });
  }

  // ======================================================================
  // ledger (GET /ledger + live tail)
  // ======================================================================
  var LEDGER = [], SEEN = {}, LAST_SEQ = 0, FRESH = {}, LTRUNC = false, LSTATUS = 'loading';
  var _lFetching = false, _lAgain = null;
  function addEvents(evts, live) {
    var added = 0, unsorted = false;
    (evts || []).forEach(function (e) {
      if (!isObj(e) || (e.game != null && e.game !== 'craps')) return;   // the ledger is shared: craps only here
      var s = +e.seq;
      if (!isFinite(s) || SEEN[s]) return;
      SEEN[s] = 1;
      if (LEDGER.length && s < LEDGER[LEDGER.length - 1].seq) unsorted = true;
      LEDGER.push(e);
      if (live) FRESH[s] = performance.now();
      added++;
    });
    if (!added) return 0;
    if (unsorted) LEDGER.sort(function (a, b) { return a.seq - b.seq; });
    if (LEDGER.length > LEDGER_MAX) {
      LEDGER.splice(0, LEDGER.length - LEDGER_MAX).forEach(function (e) { delete SEEN[e.seq]; });
      LTRUNC = true;
    }
    LAST_SEQ = LEDGER[LEDGER.length - 1].seq;
    renderLedgerSoon();
    return added;
  }
  async function fetchLedger(since) {
    if (_lFetching) { _lAgain = Math.min(_lAgain == null ? since : _lAgain, since); return; }
    _lFetching = true;
    try {
      var s = since, got = 0;
      for (var page = 0; page < 3; page++) {
        var res = await req('GET', API + '/ledger?since=' + encodeURIComponent(s) + '&limit=' + LEDGER_PAGE);
        if (res.net || !res.ok || !Array.isArray(res.d.events)) {
          if (res.status === 404 || (res.d && res.d.error === 'unknown game')) { LSTATUS = 'unavailable'; if (res.d.error === 'unknown game') noBackend(true); }
          else if (!LEDGER.length) LSTATUS = 'error';
          break;
        }
        LSTATUS = 'live';
        // The server's seq never goes backwards, and we asked for what came after events it sent us:
        // a last_seq below that means its ledger started over (config wiped) — reload it whole,
        // or every new event would look like one we already have.
        if (page === 0 && s > 0 && res.d.last_seq != null && isFinite(+res.d.last_seq) && +res.d.last_seq < s) {
          LEDGER = []; SEEN = {}; LAST_SEQ = 0; FRESH = {}; LTRUNC = false;
          s = 0; got = 0; page = -1;
          continue;
        }
        var ev = res.d.events;
        // `truncated` is also true when only the page limit cut the reply; older events are gone
        // from the server's memory when a read from 0 is truncated short of the limit (or its
        // oldest one isn't #1). The first craps seq says nothing: roulette shares the seqs.
        if (s === 0 && page === 0 && res.d.truncated && (ev.length < LEDGER_PAGE || +res.d.oldest_seq > 1)) LTRUNC = true;
        addEvents(ev, false);
        got += ev.length;
        if (ev.length < LEDGER_PAGE || got >= LEDGER_MAX) break;
        s = +ev[ev.length - 1].seq;
      }
    } finally {
      _lFetching = false;
      renderLedgerSoon();
      if (_lAgain != null) { var a = _lAgain; _lAgain = null; fetchLedger(a); }
    }
  }
  function onLedger(events) {
    if (!Array.isArray(events) || !events.length) return;
    var first = Infinity;
    events.forEach(function (e) { if (e && isFinite(+e.seq)) first = Math.min(first, +e.seq); });
    var gap = LAST_SEQ > 0 && first > LAST_SEQ + 1;
    var from = LAST_SEQ;
    // the first read failed (or never ran) but the server is clearly pushing: read the history too
    var backfill = LSTATUS !== 'live' && LSTATUS !== 'loading';
    addEvents(events, true);
    if (backfill) { _ledgerBooted = true; fetchLedger(0); }
    else if (gap) fetchLedger(from);   // missed some (socket hiccup): fill the hole
  }
  var _lRaf = false;
  function renderLedgerSoon() {
    if (_lRaf) return;
    _lRaf = true;
    requestAnimationFrame(function () { _lRaf = false; renderLedger(); });
  }
  function fmtTime(ts) {
    var d = new Date((+ts || 0) * 1000);
    if (!isFinite(d.getTime())) return '';
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') + ':' + String(d.getSeconds()).padStart(2, '0');
  }
  function renderLedger() {
    var filt = cleanUser($('cr-l-user').value).toLowerCase();
    var users = dict(), tIn = 0, tOut = 0;
    LEDGER.forEach(function (e) {
      var u = String(e.user || '');
      var a = users[u] || (users[u] = { user: u, in: 0, out: 0, n: 0 });
      var amt = +e.amount || 0;
      if (e.type === 'debit') { a.in += amt; tIn += amt; } else if (e.type === 'credit') { a.out += amt; tOut += amt; }
      a.n++;
    });
    var exp = (TABLE && TABLE.exposure) || {};
    function expOf(u) { return own(exp, u) ? +exp[u] || 0 : 0; }
    Object.keys(exp).forEach(function (u) { if (!users[u]) users[u] = { user: u, in: 0, out: 0, n: 0 }; });
    var onTable = TABLE && TABLE.total_on_table != null ? +TABLE.total_on_table
      : Object.keys(exp).reduce(function (a, u) { return a + expOf(u); }, 0);
    var house = tIn - tOut - onTable;

    // tiles
    $('cr-l-sum').innerHTML =
      tile('Coins in', fmtN(tIn), cur() + ' taken · debits') +
      tile('Coins out', fmtN(tOut), cur() + ' paid · credits') +
      tile('On the table', fmtN(onTable), cur() + ' still riding') +
      tile('House net', signed(house), house > 0 ? 'the house is up' : house < 0 ? 'the players are up' : 'even', house > 0 ? 'cr-pos' : house < 0 ? 'cr-neg' : '');

    // per-user nets
    var list = Object.keys(users).map(function (k) { var a = users[k]; a.exp = expOf(k); a.net = a.out + a.exp - a.in; return a; })
      .filter(function (a) { return a.user && (!filt || a.user.toLowerCase().indexOf(filt) >= 0); })
      .sort(function (a, b) { return (b.in + b.exp) - (a.in + a.exp) || a.user.localeCompare(b.user); });
    $('cr-l-nets').innerHTML = '<thead><tr><th>Player</th><th class="num">Bet</th><th class="num">Paid</th><th class="num">On table</th><th class="num" title="paid + on table − bet">Player net</th></tr></thead><tbody>' +
      (list.length ? list.slice(0, 200).map(function (a) {
        return '<tr><td class="wrap">@' + esc(a.user) + '</td><td class="num">' + esc(fmtN(a.in)) + '</td><td class="num">' + esc(fmtN(a.out)) +
          '</td><td class="num">' + esc(fmtN(a.exp)) + '</td><td class="num ' + (a.net > 0 ? 'cr-pos' : a.net < 0 ? 'cr-neg' : '') + '">' + esc(signed(a.net)) + '</td></tr>';
      }).join('') : '<tr><td colspan="5" class="dim">' + (filt ? 'No player matches “' + esc(filt) + '”.' : 'No players yet.') + '</td></tr>') + '</tbody>';

    // events, newest first
    var rows = [], shown = 0, matched = 0, now = performance.now();
    for (var i = LEDGER.length - 1; i >= 0; i--) {
      var e = LEDGER[i];
      if (filt && String(e.user || '').toLowerCase().indexOf(filt) < 0) continue;
      matched++;
      if (shown >= LEDGER_ROWS) continue;
      shown++;
      var cls = FRESH[e.seq] && now - FRESH[e.seq] < 1800 ? ' class="fresh"' : '';
      var deb = e.type === 'debit';
      rows.push('<tr' + cls + '><td class="num dim">' + esc(e.seq) + '</td><td class="mono dim opt" title="' + esc(new Date((+e.ts || 0) * 1000).toLocaleString()) + '">' + esc(fmtTime(e.ts)) +
        '</td><td>@' + esc(e.user) + '</td><td><span class="cr-dc ' + (deb ? 'debit' : 'credit') + '">' + (deb ? 'DEBIT' : 'CREDIT') + '</span></td>' +
        '<td class="num ' + (deb ? 'cr-neg' : 'cr-pos') + '">' + (deb ? '−' : '+') + esc(fmtN(e.amount)) + '</td>' +
        '<td>' + esc(REASONS[e.reason] || e.reason || '') + '</td><td title="' + esc(e.bet_id || '') + '">' + esc(e.bet || '') + '</td>' +
        '<td class="mono dim opt">' + esc(e.roll_id || '') + '</td></tr>');
    }
    $('cr-l-table').innerHTML = '<thead><tr><th class="num">Seq</th><th class="opt">Time</th><th>User</th><th></th><th class="num">Coins</th><th>Reason</th><th>Bet</th><th class="opt">Roll</th></tr></thead><tbody>' +
      (rows.length ? rows.join('') : '<tr><td colspan="8" class="dim">' +
        (LSTATUS === 'loading' ? 'Loading the ledger…' : LSTATUS === 'unavailable' ? 'The ledger isn\'t available on this Hexcast yet.' :
          LSTATUS === 'error' ? 'Couldn\'t read the ledger — Reload to try again.' : filt ? 'No events for “' + esc(filt) + '”.' : 'No coin movements yet.') +
        '</td></tr>') + '</tbody>';
    Object.keys(FRESH).forEach(function (s) { if (now - FRESH[s] > 2000) delete FRESH[s]; });

    // users datalist
    var dl = $('cr-l-users'), names = Object.keys(users).filter(Boolean).sort();
    var dk = names.join('\n');
    if (dl._k !== dk) { dl._k = dk; dl.innerHTML = names.slice(0, 500).map(function (n) { return '<option value="' + esc(n) + '">'; }).join(''); }

    // pill + note
    var pill = $('cr-l-pill');
    pill.textContent = LSTATUS === 'live' ? (LEDGER.length ? 'seq ' + LAST_SEQ : 'empty') : LSTATUS === 'loading' ? 'loading…' : 'unavailable';
    pill.className = 'pill' + (LSTATUS === 'live' ? ' on' : LSTATUS === 'loading' ? '' : ' off');
    var note = '';
    if (LEDGER.length) {
      note = plural(LEDGER.length, 'event') + ' loaded (#' + LEDGER[0].seq + '–#' + LAST_SEQ + ')';
      if (filt) note += ' · ' + matched + ' for “' + filt + '”';
      if (matched > LEDGER_ROWS) note += ' · showing the newest ' + LEDGER_ROWS;
      if (LTRUNC) note += ' · older events aren\'t in memory, so the nets cover this window (the full ledger is config/games_ledger.jsonl)';
    }
    $('cr-l-note').textContent = note;
  }
  function tile(label, value, sub, cls) {
    return '<div class="cr-tile"><span class="lbl">' + esc(label) + '</span><b class="' + (cls || '') + '">' + esc(value) +
      '</b><small>' + esc(sub) + '</small></div>';
  }

  // ======================================================================
  // history & stats
  // ======================================================================
  function renderRolls() {
    var list = (STATE && STATE.history) || [];
    $('cr-h-rolls').innerHTML = list.slice(0, 20).map(function (r, i) {
      if (!r) return '';
      var d = diceOf(r), t = totalOf(r);
      var title = t + ' (' + d[0] + '-' + d[1] + ')' + (r.call ? ' · ' + r.call : '') + (r.sub ? ' · ' + r.sub : '');
      return '<span class="cr-roll ' + eventClass(r) + (i === 0 ? ' first' : '') + '" title="' + esc(title) + '">' +
        '<span class="dd">' + dieHTML(d[0], 'sm') + dieHTML(d[1], 'sm') + '</span><b>' + esc(t) + '</b></span>';
    }).join('');
  }
  // The server's own tallies (GET /history -> stats) cover every roll since the last clear,
  // not just the 200 it keeps in history. null unless they're complete.
  function fromServerStats(ss) {
    if (!isObj(ss) || !isObj(ss.counts) || !isFinite(+ss.rolls)) return null;
    var keys = ['points_set', 'points_made', 'seven_outs', 'naturals', 'craps', 'hard_ways', 'longest_hand', 'current_hand'];
    for (var i = 0; i < keys.length; i++) if (!isFinite(+ss[keys[i]])) return null;
    var s = { rolls: +ss.rolls, counts: {}, naturals: +ss.naturals, craps: +ss.craps, points_set: +ss.points_set,
      points_made: +ss.points_made, seven_outs: +ss.seven_outs, hard: +ss.hard_ways, longest: +ss.longest_hand,
      current: +ss.current_hand, hands: isFinite(+ss.hands) && ss.hands != null ? +ss.hands : +ss.seven_outs, source: 'server' };
    for (var t = 2; t <= 12; t++) s.counts[t] = +ss.counts[t] || +ss.counts[String(t)] || 0;
    s.sevens = s.counts[7];
    return s;
  }
  function computeStats(list) {   // RESULT[] newest first
    var s = { rolls: 0, counts: {}, sevens: 0, naturals: 0, craps: 0, points_set: 0, points_made: 0, seven_outs: 0,
      hard: 0, longest: 0, current: 0, source: 'list' };
    for (var t = 2; t <= 12; t++) s.counts[t] = 0;
    var cur = 0;
    for (var i = (list || []).length - 1; i >= 0; i--) {
      var r = list[i];
      if (!r) continue;
      var tot = totalOf(r);
      if (!(tot >= 2 && tot <= 12)) continue;
      s.rolls++; s.counts[tot]++; cur++;
      if (tot === 7) s.sevens++;
      if (r.hard) s.hard++;
      if (r.event === 'natural') s.naturals++;
      else if (r.event === 'craps') s.craps++;
      else if (r.event === 'point_set') s.points_set++;
      else if (r.event === 'point_made') s.points_made++;
      else if (r.event === 'seven_out') { s.seven_outs++; s.longest = Math.max(s.longest, cur); cur = 0; }
    }
    s.current = cur;
    s.longest = Math.max(s.longest, cur);
    s.hands = s.seven_outs;             // a hand ends on the seven-out (the server counts it the same way)
    return s;
  }
  var _statsArgs = null, _statsHand = null;
  // "this one N rolls" follows the table's hand counter between history changes (e.g. after a clear)
  function statsFollowTable() {
    if (_statsArgs && TABLE && TABLE.hand_rolls != null && +TABLE.hand_rolls !== _statsHand) renderStats(_statsArgs[0], _statsArgs[1]);
  }
  function renderStats(results, serverStats) {
    var s = fromServerStats(serverStats) || computeStats(results);
    STATS_SRC = s.source;
    _statsArgs = [results, serverStats];
    _statsHand = TABLE && TABLE.hand_rolls != null ? +TABLE.hand_rolls : null;
    var n = s.rolls;
    $('cr-h-count').textContent = n ? plural(n, 'roll') + ' counted' : '';
    $('cr-h-n').textContent = n ? 'n = ' + n : '';
    var maxPct = 100 * 6 / 36;
    for (var t = 2; t <= 12; t++) if (n) maxPct = Math.max(maxPct, 100 * s.counts[t] / n);
    var top = maxPct * 1.12;
    var cols = [];
    for (t = 2; t <= 12; t++) {
      var pct = n ? 100 * s.counts[t] / n : 0, ex = 100 * WAYS[t] / 36;
      cols.push('<div class="col" title="' + t + ' — ' + s.counts[t] + ' of ' + n + ' rolls (' + pct.toFixed(1) + '%) · expected ' + ex.toFixed(1) + '%">' +
        (pct > 0 ? '<i class="bar" style="height:' + (pct / top * 100).toFixed(2) + '%"></i>' : '') +
        '<i class="exp" style="bottom:calc(' + (ex / top * 100).toFixed(2) + '% - 1px)"></i></div>');
    }
    var step = top > 60 ? 20 : top > 30 ? 10 : 5, lines = '';
    for (var g = step; g < top; g += step) lines += '<i class="gl" style="bottom:' + (g / top * 100).toFixed(2) + '%"><span>' + g + '%</span></i>';
    $('cr-h-histo').innerHTML = lines + cols.join('');
    var decided = s.points_made + s.seven_outs;
    function st(label, value, small) { return '<div class="cr-stat"><span class="lbl">' + esc(label) + '</span><b>' + esc(value) + '</b>' + (small ? '<small>' + esc(small) + '</small>' : '') + '</div>'; }
    $('cr-h-stats').innerHTML =
      st('Rolls', n) +
      st('Sevens', s.sevens, n ? (100 * s.sevens / n).toFixed(1) + '% · fair 16.7%' : '') +
      st('Naturals', s.naturals, 'come-out 7 / 11') +
      st('Craps', s.craps, 'come-out 2 / 3 / 12') +
      st('Points set', s.points_set) +
      st('Points made', s.points_made, decided ? Math.round(100 * s.points_made / decided) + '% of decided' : '') +
      st('Seven-outs', s.seven_outs, 'shooter\'s hand over') +
      st('Hands', s.hands, 'played out · this one ' + plural(TABLE && TABLE.hand_rolls != null ? +TABLE.hand_rolls || 0 : s.current, 'roll')) +
      st('Longest hand', s.longest, 'rolls') +
      st('Hard ways', s.hard, 'pairs of 4/6/8/10');
  }
  var _statsKey = null, _statsT = null, _statsSeq = 0, STATS_LIST = null, STATS_SRC = null;
  function maybeFetchStats(st) {
    var key = histKey(st.history) + '|' + ((st.last && st.last.id) || '');
    if (key === _statsKey) return;
    _statsKey = key;
    clearTimeout(_statsT);
    _statsT = setTimeout(fetchStats, 150);
  }
  // Server stats first (one tiny request); only if they're missing, count the last 200 rolls here.
  async function fetchStats() {
    var seq = ++_statsSeq;
    var res = await req('GET', API + '/history?limit=1');
    if (seq !== _statsSeq) return;
    if (res.ok && fromServerStats(res.d.stats)) { STATS_LIST = null; renderStats(null, res.d.stats); return; }
    res = await req('GET', API + '/history?limit=200');
    if (seq !== _statsSeq) return;
    if (res.ok && Array.isArray(res.d.history)) {
      STATS_LIST = res.d.history.map(function (h) { return h && h.result; }).filter(Boolean);
    } else STATS_LIST = null;
    renderStats(STATS_LIST || (STATE && STATE.history) || []);
  }

  // ======================================================================
  // settings (everything that isn't APPEARANCE)
  // ======================================================================
  var SET = [
    { k: 'roll_seconds', t: 'num', min: 2.5, max: 10 },
    { k: 'result_seconds', t: 'num', min: 1, max: 120 },
    { k: 'cooldown_seconds', t: 'num', min: 0, max: 3600 },
    { k: 'bet_window_seconds', t: 'num', min: 5, max: 300 },
    { k: 'currency', t: 'text', max: 24 },
    { k: 'min_bet', t: 'int', min: 1, max: 1e9 },
    { k: 'max_bet', t: 'int', min: 0, max: 1e12 },
    { k: 'odds_rule', t: 'select' },
    { k: 'field_12_pays', t: 'intsel' },
    { k: 'roll_clip', t: 'text', max: 200 },
    { k: 'land_clip', t: 'text', max: 200 },
    { k: 'hide_when_idle', t: 'check' },
    { k: 'show_when_bets', t: 'check' },
    { k: 'auto_roll', t: 'check' },
    // sound is also in the Edit-Mode editor (it's per-roll overridable); here like roulette's Settings
    { k: 'sfx', t: 'check' },
    { k: 'sfx_volume', t: 'num', min: 0, max: 1 }
  ];
  var DIRTY = {};
  function paintDirty() { $('cr-s-dirty').textContent = Object.keys(DIRTY).length ? 'unsaved changes' : ''; }
  function paintVol() {
    var el = $('cr-s-sfx_volume'), out = $('cr-s-sfx_volume-val');
    if (el && out) out.textContent = Math.round(clamp(num(el.value, 0.5), 0, 1) * 100) + '%';
  }
  function fillOne(f, c) {
    var el = $('cr-s-' + f.k); if (!el) return;
    if (f.t === 'check') el.checked = !!c[f.k];
    else el.value = (c[f.k] == null ? '' : String(c[f.k]));
    if (f.k === 'sfx_volume') paintVol();
  }
  function fillSettings(force) {
    var c = cfgFull();
    SET.forEach(function (f) {
      var el = $('cr-s-' + f.k); if (!el) return;
      if (!force && (DIRTY[f.k] || el === document.activeElement)) return;
      fillOne(f, c);
    });
    paintDirty();
  }
  function collectSettings() {
    var out = {};
    SET.forEach(function (f) {
      var el = $('cr-s-' + f.k); if (!el) return;
      if (f.t === 'check') { out[f.k] = el.checked; return; }
      if (f.t === 'num' || f.t === 'int') {
        var v = parseFloat(el.value);
        if (!isFinite(v)) return;                 // blank / junk: leave the saved value alone
        if (f.t === 'int') v = Math.round(v);
        out[f.k] = clamp(v, f.min, f.max); return;
      }
      if (f.t === 'intsel') { out[f.k] = parseInt(el.value, 10) === 2 ? 2 : 3; return; }
      if (f.t === 'select') { out[f.k] = String(el.value); return; }
      var s = String(el.value || '').trim().slice(0, f.max || 200);
      if (f.k === 'currency' && !s) return;       // 1..24 chars: blank keeps the saved name
      out[f.k] = s;
    });
    return out;
  }
  async function saveSettings() {
    var out = collectSettings();
    var min = out.min_bet != null ? out.min_bet : cfgFull().min_bet;
    if (out.max_bet != null && out.max_bet !== 0 && out.max_bet < min) {
      toast('Max bet must be 0 (no max) or at least the min bet (' + fmtN(min) + ')', 3000);
      $('cr-s-max_bet').focus();
      return;
    }
    var d = await api('POST', '/games/api/config', { craps: out }, "Can't save");
    if (!d) return;
    DIRTY = {};
    var conf = d.config || (d.craps ? { craps: d.craps } : null);
    if (conf && isObj(conf.craps)) onConfig(conf);
    else CFG_RAW = Object.assign({}, CFG_RAW || {}, out);
    fillSettings(true);
    toast('Saved');
  }

  // ======================================================================
  // API card
  // ======================================================================
  function buildApiCard() {
    var rows = [
      ['GET|POST /games/api/craps/roll', 'throw the dice (aliases <code>/spin</code>, <code>/play</code>). <code>user</code> (becomes the shooter if there is none), <code>wait</code> (reply after landing, with <code>settlements</code> + <code>credits</code> committed), <code>test</code> (animation only: no settlement, ledger or history), <code>duration</code> (2.5–10 s), <code>overrides</code> or <code>x</code>/<code>y</code>/<code>scale</code>, optional <code>bets:[…]</code> placed first (<code>placed</code> in the reply). Returns the roll: <code>result</code> (dice, total, call), <code>settlements</code>, <code>credits</code>, <code>summary</code>, <code>shooter</code>, <code>lands_at</code> and <code>table</code> (after landing with <code>wait</code>, else the pre-roll table)'],
      ['GET|POST /games/api/craps/bet', '<code>{user, bet, amount, target?}</code> or <code>{bets:[…]}</code> (≤200) → <code>accepted</code>, <code>rejected</code> (with <code>error</code>), <code>debits:[{user,amount,bet_id,seq}]</code>, <code>table</code>. 400 if every bet was rejected'],
      ['GET|POST /games/api/craps/remove', '<code>{bet_id}</code>, <code>{user, bet}</code> or <code>{user, all:true}</code> → <code>removed</code>, <code>credits</code> (refunds), <code>ledger</code>. Contract bets → 400 (their odds can come down: <code>bet=odds</code>)'],
      ['GET|POST /games/api/craps/clear', 'refund every bet (credits, reason <code>refund</code>), back to the come-out, no shooter'],
      ['GET /games/api/craps/table', 'the table: phase, point, shooter, hand_rolls, bets, exposure, total_on_table, bets_open, auto_roll_in_ms, last_seq'],
      ['GET /games/api/craps/user/{name}', 'that user\'s bets, exposure and session debits / credits / net'],
      ['GET /games/api/craps/ledger?since=0&limit=500', 'the craps coin movements with <code>seq</code> &gt; since, oldest first (limit ≤ 5000): <code>events</code>, <code>last_seq</code>, <code>truncated</code>. The ledger is shared with roulette (one seq space, each event carries <code>game</code>), so gaps in the craps seqs are normal'],
      ['GET /games/api/ledger?since=0&game=', 'every game\'s events in one stream; <code>game=craps</code> or <code>roulette</code> for one — a bank running both games tails this once'],
      ['GET|POST /games/api/craps/timer', 'start (or restart) the countdown now — <code>seconds</code> (5–300, default the bet window), with or without bets, auto-roll on or off. At 0 the dice go by themselves (the shooter throws). 409 busy unless idle → <code>auto_in_ms</code>, <code>table</code>, <code>state</code>. A running countdown keeps the table on screen'],
      ['GET|POST /games/api/craps/timer/cancel', 'stop the countdown (auto-roll or started with /timer) → <code>cancelled</code>, <code>table</code>, <code>state</code>'],
      ['GET /games/api/craps/validate', '<code>?bet=&amp;user=&amp;amount=&amp;target=</code> — a dry run against the table now: <code>valid</code>, type, label, <code>odds_text</code>, <code>hint</code> (amounts that pay exactly), <code>max_odds</code> or <code>error</code>'],
      ['GET /games/api/craps/bets', 'bet reference: type, label, syntax, pays, when, one_roll + the rules'],
      ['GET /games/api/craps/last · history?limit=20', 'last committed roll / landed rolls newest first'],
      ['POST /games/api/craps/history/clear', 'clear the roll history (bets and ledger are untouched)'],
      ['GET|POST /games/api/craps/announce', 'Hex does the math: Hex\'s own payouts card for the roll on screen (or the last one), in place of the computed one. <code>title</code>, <code>lines:[{user,amount,text}]</code> (≤50; one line as <code>?user=&amp;amount=&amp;text=</code>), <code>empty_text</code>, <code>seconds</code> (1–120, default: the rest of the result), <code>currency</code> (default: the table\'s), <code>spin_id</code> (not that roll → 409 <code>{"error":"stale"}</code>). Posted mid-roll it waits for the landing; a new roll clears it. Display only: never bets, ledger, history or stats'],
      ['GET|POST /games/api/craps/announce/clear', 'take Hex\'s payouts card down now'],
      ['GET|POST /games/api/craps/board', 'Hex\'s own "on the table" board, shown in place of this table\'s bets: <code>{title, bets:[{user,text,amount}]}</code> (≤100, amount ≥ 0) → <code>table.display_board</code> <code>{title, bets, total}</code> (memory only). <code>{"bets":[]}</code> = an empty board: Hex has nothing down, so no bets board shows (this table\'s own stays away too). A board with lines counts for <code>show_when_bets</code>. Allowed while the dice fly'],
      ['GET|POST /games/api/craps/board/clear', 'back to the board computed from this table\'s bets (same as <code>{"clear":true}</code>)'],
      ['GET|POST /games/api/craps/show · hide · stop', 'show the idle table / hide it (409 while the dice fly) / abort (a roll in the air still settles)'],
      ['GET /games/api/status', 'overlay count + live state per game (craps state carries the <code>table</code>)'],
      ['GET|POST /games/api/config', 'read / merge-save <code>{"craps":{…}}</code>'],
      ['WS /games/ws/panel', '<code>config</code> / <code>state</code> (with <code>table</code>) / <code>stop</code> and <code>{"type":"ledger","game":"craps","events":[…]}</code> (one message per game) — a bank bot can listen here instead of polling']
    ];
    $('cr-api-table').innerHTML = rows.map(function (r) { return '<tr><td>' + esc(r[0]) + '</td><td>' + r[1] + '</td></tr>'; }).join('');
    var c = function (s) { return s.split(' ').map(function (x) { return '<code>' + esc(x) + '</code>'; }).join(' '); };
    var bets = [
      ['Pass line', c('pass passline line pl'), '1 to 1', 'come-out only; a contract bet once the point is set'],
      ['Don\'t pass', c('dontpass dp'), '1 to 1 (12 pushes)', 'come-out only; can be taken down'],
      ['Come', c('come'), '1 to 1', 'point phase only; travels to its number, then contract'],
      ['Don\'t come', c('dontcome dc'), '1 to 1 (12 pushes)', 'point phase only'],
      ['Pass odds', c('odds odds:pass passodds'), '2:1 on 4/10 · 3:2 on 5/9 · 6:5 on 6/8', 'point phase, behind your pass bet; limit by the odds rule'],
      ['Lay odds (don\'t pass)', c('odds:dontpass layodds dpodds'), '1:2 · 2:3 · 5:6', 'point phase, behind your don\'t pass; always working'],
      ['Come odds', c('odds:6 odds:come6 comeodds:6'), 'true odds', 'on a come bet that travelled; OFF on a come-out roll'],
      ['Don\'t come odds', c('odds:dontcome6 layodds:6 dcodds:6'), 'lay odds', 'on a don\'t come bet on that number'],
      ['Place', c('place6 place:6 p6 6'), '9:5 on 4/10 · 7:5 on 5/9 · 7:6 on 6/8, stays up', 'anytime; OFF on a come-out roll'],
      ['Hard way', c('hard6 hard:6 h6 hardsix'), '7:1 on 4/10 · 9:1 on 6/8, stays up', 'anytime (4, 6, 8, 10); OFF on a come-out roll'],
      ['Field', c('field'), '1:1 on 3/4/9/10/11 · 2:1 on 2 · 12 pays 3:1 (or 2:1)', 'one roll'],
      ['Any seven', c('any7 anyseven seven 7'), '4 to 1', 'one roll'],
      ['Any craps', c('anycraps craps ac'), '7 to 1', 'one roll (2, 3, 12)'],
      ['Aces / boxcars', c('aces snakeeyes 2 boxcars midnight 12'), '30 to 1', 'one roll'],
      ['Ace-deuce / yo', c('acedeuce 3 yo eleven 11'), '15 to 1', 'one roll'],
      ['Horn', c('horn'), '2/12 net 27:4 · 3/11 net 3:1', 'one roll; amount a multiple of 4'],
      ['C &amp; E', c('ce c&e crapseleven'), 'craps net 3:1 · 11 net 7:1', 'one roll; amount even']
    ];
    $('cr-bet-table').innerHTML = bets.map(function (b) {
      return '<tr><td>' + b[0] + '</td><td>' + b[1] + '</td><td>' + esc(b[2]) + '</td><td>' + esc(b[3]) + '</td></tr>';
    }).join('');
    var base = location.protocol + '//' + location.host;
    var J = function (o) { return '"' + JSON.stringify(o).replace(/"/g, '\\"') + '"'; };
    $('cr-api-curl').textContent = [
      'curl -X POST ' + base + '/games/api/craps/bet -H "Content-Type: application/json" -d ' + J({ user: 'alice', bet: 'pass', amount: 100 }),
      'curl "' + base + '/games/api/craps/bet?user=bob&bet=place6&amount=60"',
      'curl "' + base + '/games/api/craps/validate?user=alice&bet=odds&amount=250"',
      'curl -X POST ' + base + '/games/api/craps/roll -H "Content-Type: application/json" -d ' + J({ user: 'alice', wait: true }),
      'curl "' + base + '/games/api/craps/ledger?since=0&limit=500"',
      'curl ' + base + '/games/api/craps/table',
      'curl ' + base + '/games/api/craps/user/alice',
      'curl -X POST ' + base + '/games/api/craps/remove -H "Content-Type: application/json" -d ' + J({ bet_id: 'b-1a2b3c4d' }),
      'curl -X POST ' + base + '/games/api/craps/clear',
      'curl -X POST ' + base + '/games/api/craps/board -H "Content-Type: application/json" -d ' + J({ bets: [{ user: 'alice', text: 'Pass 100 + odds 50', amount: 150 }] }),
      'curl -X POST ' + base + '/games/api/craps/announce -H "Content-Type: application/json" -d ' + J({ title: 'WINNERS', lines: [{ user: 'alice', amount: 200, text: 'Pass line' }] })
    ].join('\n');
    $('cr-bank').innerHTML = [
      '<b>Hex is the bank.</b> Hexcast never holds a balance — Hex\'s <b>hexbank</b> does. Hex takes the chat commands, calls this API and moves the coins; Hexcast only says exactly whose coins to take and pay. (<code>hexbank.balance</code> / <code>debit</code> / <code>credit</code> below are placeholder names for Hex\'s own functions.) Always send the same form of a name — the lowercase login is a good choice.',
      '<b>What Hex saves</b>, in the same transaction as each hexbank change: <code>last_seq</code> (the last ledger event it processed) and <code>done</code> (seqs above it that it already handled straight from a reply, so the tail skips them). A new ledger starts at 0; if it already has test events, press <b>Refund all &amp; clear table</b> first and start at <code>table.last_seq</code>. Handle one craps money action at a time (one lock around bets, take-downs and the tail).',
      '<b>Placing a bet, way A — check first:</b> on <code>!bet pass 100</code>: sync the ledger, check <code>hexbank.balance(user) ≥ 100</code> → <code>POST /games/api/craps/bet</code> <code>{"user","bet","amount"}</code> → for every <code>debits[]</code> entry <code>{user, amount, bet_id, seq}</code>: <code>hexbank.debit(user, amount)</code> and add its <code>seq</code> to <code>done</code>. Rejected bets moved nothing: reply with <code>rejected[].error</code>. If a debit fails, take it back down (<code>POST /remove {"bet_id"}</code>, or <code>{"user","bet"}</code> with the odds text for odds) and add both seqs — the failed debit\'s and the refund\'s (in the reply\'s <code>ledger[]</code>) — to <code>done</code>; if the refund (<code>removed[].refund</code>) is more than the failed debit, <code>hexbank.credit</code> the difference: Hex took that part earlier.',
      '<b>Placing a bet, way B — debit first:</b> <code>hexbank.debit(user, 100)</code> → <code>POST /bet</code> → add the <code>debits[]</code> seqs to <code>done</code> and <code>hexbank.credit</code> back the rest: the rejected part, or all of it on a 400, a 409 <code>bets_closed</code> (dice in the air) or no reply.',
      '<b>Tail the ledger:</b> <code>GET /games/api/craps/ledger?since=&lt;last_seq&gt;</code> every second or two (or on each <code>{"type":"ledger"}</code> push on <code>WS /games/ws/panel</code>, and once on every connect). For each event, oldest first: seq in <code>done</code> → skip it; <code>credit</code> → <code>hexbank.credit(user, amount)</code>; <code>debit</code> → <code>hexbank.debit(user, amount)</code> (a bet Hex didn\'t place itself: this panel\'s <b>Place a bet</b>, a curl, a <code>/bet</code> whose reply never arrived); then save its <code>seq</code> as <code>last_seq</code>. <code>seq</code> is unique and strictly increasing, so every coin moves exactly once across restarts of either side.',
      '<b>Why the ledger, not the replies:</b> rolls, take-downs and refunds also happen without Hex asking — this panel\'s Roll / Take down / Refund all, auto-roll, a ⏹ Stop mid-roll. The ledger has every one. <code>!roll</code> → <code>POST /games/api/craps/roll {"user","wait":true}</code> returns after landing with <code>result.call</code>, <code>settlements[]</code> and <code>credits[]</code> — use those to <i>announce</i> in chat, then sync the tail; never pay from the reply.',
      '<b>Restarts:</b> the ledger (<code>config/games_ledger.jsonl</code>) and the table are saved on disk; <code>seq</code> carries on and bets stay on the felt. A roll in the air during a restart is cancelled — nothing was committed, so pay nothing and roll again.',
      '<b>Whole coins:</b> winnings are rounded <i>down</i> — pay exactly the ledger amounts, never recompute. Suggest amounts that pay exactly: place 6/8 in 6s, place 4/5/9/10 in 5s, odds on 6/8 in 5s, odds on 5/9 even, horn in 4s, C&amp;E even. <code>/validate</code> returns a <code>hint</code> when an amount doesn\'t pay exactly.',
      '<b>Check your books:</b> for every user, debits − credits = coins on the table + net losses; house net = Σdebits − Σcredits − Σon table (the Ledger card above shows both). The full guide with a sample bot loop is in <code>docs/craps.md</code>.'
    ].map(function (s) { return '<li>' + s + '</li>'; }).join('');
  }

  // ======================================================================
  // Edit Mode: placement & look editor (the roulette / soundboard openEditor())
  // ======================================================================
  var ED = null;
  function sampleTable(cfg) {
    return { phase: 'point', point: 6, shooter: 'hexcast', hand_rolls: 3, bets_open: true, auto_roll_in_ms: null,
      currency: cfg.currency, min_bet: cfg.min_bet, max_bet: cfg.max_bet, odds_rule: cfg.odds_rule, last_seq: 0,
      bets: [
        { id: 'b-demo0001', user: 'alice', type: 'pass', number: null, amount: 100, odds: 50, label: 'Pass line', working: true, removable: false, one_roll: false },
        { id: 'b-demo0002', user: 'alice', type: 'place', number: 8, amount: 60, odds: 0, label: 'Place 8', working: true, removable: true, one_roll: false },
        { id: 'b-demo0003', user: 'bob', type: 'field', number: null, amount: 25, odds: 0, label: 'Field', working: true, removable: true, one_roll: true },
        { id: 'b-demo0004', user: 'carol', type: 'hard', number: 6, amount: 10, odds: 0, label: 'Hard 6', working: true, removable: true, one_roll: false },
        { id: 'b-demo0005', user: 'dave', type: 'dont_pass', number: null, amount: 50, odds: 0, label: 'Don\'t pass', working: true, removable: true, one_roll: false }
      ],
      exposure: { alice: 210, bob: 25, carol: 10, dave: 50 }, total_on_table: 295 };
  }
  // Demo settlement of the sample table (preview only — the server's engine settles real bets).
  function demoSettle(table, r, cfg) {
    var t = r.total, pt = table.point, out = [];
    var TRUE = { 4: [2, 1], 5: [3, 2], 6: [6, 5], 8: [6, 5], 9: [3, 2], 10: [2, 1] };
    var PLACE = { 4: [9, 5], 5: [7, 5], 6: [7, 6], 8: [7, 6], 9: [7, 5], 10: [9, 5] };
    table.bets.forEach(function (b) {
      var o = null, won = 0, stays = false, a = +b.amount || 0, od = +b.odds || 0;
      if (b.type === 'pass') { if (t === pt) { o = 'win'; won = a + Math.floor(od * TRUE[pt][0] / TRUE[pt][1]); } else if (t === 7) o = 'lose'; }
      else if (b.type === 'dont_pass') { if (t === 7) { o = 'win'; won = a; } else if (t === pt) o = 'lose'; }
      else if (b.type === 'place') { if (t === b.number) { o = 'win'; stays = true; won = Math.floor(a * PLACE[t][0] / PLACE[t][1]); } else if (t === 7) o = 'lose'; }
      else if (b.type === 'hard') { if (t === b.number && r.pair) { o = 'win'; stays = true; won = a * (t === 6 || t === 8 ? 9 : 7); } else if (t === b.number || t === 7) o = 'lose'; }
      else if (b.type === 'field') {
        if ([3, 4, 9, 10, 11].indexOf(t) >= 0) { o = 'win'; won = a; }
        else if (t === 2) { o = 'win'; won = 2 * a; }
        else if (t === 12) { o = 'win'; won = (+cfg.field_12_pays === 2 ? 2 : 3) * a; }
        else o = 'lose';
      }
      if (!o) return;
      out.push({ bet_id: b.id, user: b.user, type: b.type, label: b.label, number: b.number, amount: a, odds: od,
        outcome: o, won: won, credit: o === 'win' ? (stays ? won : a + od + won) : 0, stays: stays, note: 'preview' });
    });
    return out;
  }
  function sampleHistory() {
    var pairs = [[3, 4], [2, 2], [6, 5], [1, 3], [4, 4], [5, 2], [3, 3], [1, 1], [6, 6], [2, 5]];
    return pairs.map(function (p) { return resultFor(p[0], p[1], 'come_out', null); });
  }
  function selectRow(id, label, opts) {
    return '<div class="editor-row"><label>' + label + '</label><select id="' + id + '">' +
      opts.map(function (o) { return '<option value="' + o[0] + '">' + esc(o[1]) + '</option>'; }).join('') + '</select></div>';
  }
  function openEditor() {
    if (ED) return;
    var base = cfgFull();
    var w = pick(base, EDIT_KEYS);
    w.x = clamp(num(w.x, 50), 0, 100);
    w.y = clamp(num(w.y, 50), 0, 100);
    w.scale = clamp(num(w.scale, 1), 0.2, 5);
    w.history_count = clamp(Math.round(num(w.history_count, 10)), 1, 20);
    w.bets_max = clamp(Math.round(num(w.bets_max, 6)), 1, 20);
    w.payouts_max = clamp(Math.round(num(w.payouts_max, 5)), 1, 20);
    w.sfx_volume = clamp(num(w.sfx_volume, 0.5), 0, 1);

    var ov = document.createElement('div');
    ov.className = 'modal-overlay';
    ov.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <span class="title">Craps — placement &amp; look</span>
        <button class="close" title="Close">×</button>
      </div>
      <div class="canvas-preview" id="cr-ed-preview">
        <div class="grid-overlay"></div>
        <div class="gstage"><div class="gwheel cr-box" id="cr-ed-box"></div></div>
        <div class="pmsg" id="cr-ed-msg" style="display:none"></div>
      </div>
      <div class="note" style="margin-top:-4px">Sample bets on a sample table — ▶ Preview throws local demo dice; nothing is bet, rolled on stream or saved.</div>
      <div class="editor-controls">
        <div class="editor-row">
          <label>Scale</label>
          <input type="range" id="cr-ed-scale" min="0.2" max="5" step="0.05" value="${w.scale}">
          <div class="value" id="cr-ed-scaleval">${w.scale.toFixed(2)}x</div>
        </div>
        <div class="editor-row">
          <label>Position</label>
          <div class="value" style="flex:1;text-align:left;">
            x:<span id="cr-ed-x">${w.x.toFixed(0)}</span>% y:<span id="cr-ed-y">${w.y.toFixed(0)}</span>%
          </div>
          <div class="quick-positions">
            <button data-pos="15,15" title="Top Left">↖</button>
            <button data-pos="50,15" title="Top">↑</button>
            <button data-pos="85,15" title="Top Right">↗</button>
            <button data-pos="15,50" title="Left">←</button>
            <button data-pos="50,50" title="Center">●</button>
            <button data-pos="85,50" title="Right">→</button>
            <button data-pos="15,85" title="Bottom Left">↙</button>
            <button data-pos="50,85" title="Bottom">↓</button>
            <button data-pos="85,85" title="Bottom Right">↘</button>
          </div>
        </div>
        ${selectRow('cr-ed-theme', 'Theme', THEMES)}
        ${selectRow('cr-ed-dice_style', 'Dice', DICE)}
        <div class="editor-row wrap">
          <label>Table</label>
          <label class="tog" style="min-width:122px"><input type="checkbox" id="cr-ed-show_point"> point row &amp; puck</label>
          <label class="tog"><input type="checkbox" id="cr-ed-show_user"> shooter caption</label>
        </div>
        <div class="editor-row wrap">
          <label>History</label>
          <label class="tog" style="min-width:122px"><input type="checkbox" id="cr-ed-show_history"> show strip</label>
          <input type="number" id="cr-ed-history_count" min="1" max="20" step="1" style="flex:0 0 80px">
          <span class="value" style="min-width:0;text-align:left">rolls</span>
        </div>
        <div class="editor-row wrap">
          <label>Bets</label>
          <label class="tog" style="min-width:122px"><input type="checkbox" id="cr-ed-show_bets"> bets board</label>
          <input type="number" id="cr-ed-bets_max" min="1" max="20" step="1" style="flex:0 0 80px">
          <span class="value" style="min-width:0;text-align:left">players</span>
        </div>
        <div class="editor-row wrap">
          <label>Payouts</label>
          <label class="tog" style="min-width:122px"><input type="checkbox" id="cr-ed-show_payouts"> winners board</label>
          <input type="number" id="cr-ed-payouts_max" min="1" max="20" step="1" style="flex:0 0 80px">
          <span class="value" style="min-width:0;text-align:left">winners</span>
        </div>
        <div class="editor-row">
          <label>Rules</label>
          <label class="tog"><input type="checkbox" id="cr-ed-show_rules"> how-to-play box (while bets are open)</label>
        </div>
        <div class="editor-row">
          <label>Sound</label>
          <label class="tog" style="min-width:122px"><input type="checkbox" id="cr-ed-sfx"> dice sounds</label>
          <input type="range" id="cr-ed-sfx_volume" min="0" max="1" step="0.05">
          <div class="value" id="cr-ed-volval">50%</div>
        </div>
      </div>
      <div class="modal-actions">
        <div class="left">
          <button class="reset">Reset</button>
          <button class="preview">▶ Preview</button>
          <button class="test">Test in OBS</button>
        </div>
        <div class="right">
          <button class="cancel">Cancel</button>
          <button class="save">Save</button>
        </div>
      </div>
    </div>`;
    document.body.appendChild(ov);

    var q = function (sel) { return ov.querySelector(sel); };
    var preview = q('#cr-ed-preview');
    var view = makeView(preview, q('.gstage'), q('#cr-ed-box'));
    var slider = q('#cr-ed-scale');
    function full() { return Object.assign({}, cfgFull(), w); }
    var table0 = sampleTable(full());

    // Real renderer, sound off, sample table + the real (or a sample) history.
    var c = C();
    if (c && typeof c.create === 'function') {
      try { view.inst = c.create(view.el, full(), { sound: false }); }
      catch (e) { console.error('[craps] renderer create failed:', e); view.inst = null; }
    }
    if (!view.inst) {
      view.el.classList.add('empty');
      viewMsg(q('#cr-ed-msg'), 'Craps renderer not loaded — placement still works.');
    } else {
      if (typeof view.inst.setTable === 'function') safe(function () { view.inst.setTable(table0); });
      var hist = (STATE && STATE.history && STATE.history.length) ? STATE.history : sampleHistory();
      if (typeof view.inst.setHistory === 'function') safe(function () { view.inst.setHistory(hist); });
    }

    function update() {
      placeView(view, w.x, w.y, w.scale);
      q('#cr-ed-x').textContent = w.x.toFixed(0);
      q('#cr-ed-y').textContent = w.y.toFixed(0);
      q('#cr-ed-scaleval').textContent = w.scale.toFixed(2) + 'x';
    }
    update();

    var _cfgPending = false;
    function pushCfg() {
      if (_cfgPending || !view.inst) return;
      _cfgPending = true;
      requestAnimationFrame(function () {
        _cfgPending = false;
        if (!view.inst) return;
        safe(function () { view.inst.setConfig(full()); });
        requestResize(view);
      });
    }

    // Drag-to-position (pointer capture), exactly like the soundboard.
    var previewActive = false;
    var dragging = false, startX, startY, startMx, startMy;
    view.el.addEventListener('pointerdown', function (e) {
      if (previewActive) return;
      dragging = true;
      startX = w.x; startY = w.y;
      startMx = e.clientX; startMy = e.clientY;
      try { view.el.setPointerCapture(e.pointerId); } catch (_) {}
      preview.classList.add('dragging');
      e.preventDefault();
    });
    view.el.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var rect = preview.getBoundingClientRect();
      w.x = clamp(startX + ((e.clientX - startMx) / rect.width) * 100, 0, 100);
      w.y = clamp(startY + ((e.clientY - startMy) / rect.height) * 100, 0, 100);
      update();
    });
    function endDrag(e) {
      if (!dragging) return;
      dragging = false;
      preview.classList.remove('dragging');
      try { view.el.releasePointerCapture(e.pointerId); } catch (_) {}
    }
    view.el.addEventListener('pointerup', endDrag);
    view.el.addEventListener('pointercancel', endDrag);

    slider.oninput = function () { w.scale = clamp(num(slider.value, 1), 0.2, 5); update(); };
    ov.querySelectorAll('.quick-positions button').forEach(function (b) {
      b.onclick = function () { var p = b.dataset.pos.split(',').map(Number); w.x = p[0]; w.y = p[1]; update(); };
    });

    // Appearance rows
    var sels = { theme: q('#cr-ed-theme'), dice_style: q('#cr-ed-dice_style') };
    var cbs = ['show_point', 'show_user', 'show_history', 'show_bets', 'show_payouts', 'show_rules', 'sfx'];
    var nums = ['history_count', 'bets_max', 'payouts_max'];
    var vol = q('#cr-ed-sfx_volume');
    function paintVol() { q('#cr-ed-volval').textContent = Math.round(w.sfx_volume * 100) + '%'; }
    function syncControls() {
      slider.value = w.scale;
      Object.keys(sels).forEach(function (k) {
        var s = sels[k]; s.value = w[k];
        if (s.value !== w[k]) { s.value = DEFAULTS[k]; w[k] = DEFAULTS[k]; }
      });
      cbs.forEach(function (k) { q('#cr-ed-' + k).checked = !!w[k]; });
      nums.forEach(function (k) { q('#cr-ed-' + k).value = w[k]; });
      vol.value = w.sfx_volume;
      paintVol();
    }
    syncControls();
    Object.keys(sels).forEach(function (k) { sels[k].onchange = function () { w[k] = sels[k].value; pushCfg(); }; });
    cbs.forEach(function (k) { q('#cr-ed-' + k).onchange = function () { w[k] = this.checked; pushCfg(); }; });
    nums.forEach(function (k) {
      var el = q('#cr-ed-' + k);
      el.oninput = function () { var v = parseInt(el.value, 10); if (!isFinite(v)) return; w[k] = clamp(v, 1, 20); pushCfg(); };
      el.onchange = function () { el.value = w[k]; };
    });
    vol.oninput = function () { w.sfx_volume = clamp(num(vol.value, 0.5), 0, 1); paintVol(); pushCfg(); };

    // ---- ▶ Preview: a local demo roll on the sample table (resultFor + random seed; no server call) ----
    var previewBtn = q('.modal-actions .preview');
    var previewTok = 0, previewTimer = null;
    function previewDone(tok) {
      if (tok !== previewTok) return;
      clearTimeout(previewTimer);
      previewActive = false;
      previewBtn.classList.remove('playing');
      previewBtn.textContent = '▶ Preview';
    }
    function stopPreview() {
      if (!previewActive) return;
      previewTok++;
      clearTimeout(previewTimer);
      previewActive = false;
      previewBtn.classList.remove('playing');
      previewBtn.textContent = '▶ Preview';
      if (view.inst) {
        safe(function () { view.inst.reset(); });
        if (typeof view.inst.setTable === 'function') safe(function () { view.inst.setTable(table0); });
      }
    }
    if (!view.inst) { previewBtn.disabled = true; previewBtn.title = 'craps renderer not loaded'; }
    previewBtn.onclick = function () {
      if (!view.inst) return;
      if (previewActive) { stopPreview(); return; }
      var cfg = full();
      var d1 = 1 + Math.floor(Math.random() * 6), d2 = 1 + Math.floor(Math.random() * 6);
      var result = resultFor(d1, d2, table0.phase, table0.point);
      var dur = Math.round(clamp(num(cfg.roll_seconds, 4), 2.5, 10) * 1000);
      var settlements = demoSettle(table0, result, cfg);
      var credits = dict();
      settlements.forEach(function (s) { if (s.credit) credits[s.user] = (credits[s.user] || 0) + s.credit; });
      var now = Date.now() / 1000;
      var spin = {
        id: 'preview-' + Date.now().toString(36), game: 'craps', result: result, user: table0.shooter, shooter: table0.shooter,
        test: true, seed: Math.floor(Math.random() * 2147483647),
        duration_ms: dur, result_ms: Math.round(num(cfg.result_seconds, 5) * 1000), elapsed_ms: 0,
        started_at: now, lands_at: now + dur / 1000,
        settlements: settlements,
        credits: Object.keys(credits).map(function (u) { return { user: u, amount: credits[u] }; }),
        summary: { bets_settled: settlements.length,
          winners: settlements.filter(function (s) { return s.outcome === 'win'; }).length,
          total_won: settlements.reduce(function (a, s) { return a + s.won; }, 0),
          total_lost: settlements.reduce(function (a, s) { return a + (s.outcome === 'lose' ? s.amount + s.odds : 0); }, 0),
          total_credited: settlements.reduce(function (a, s) { return a + s.credit; }, 0) },
        overrides: {}
      };
      previewActive = true;
      previewBtn.classList.add('playing');
      previewBtn.textContent = '⏸ Stop';
      var tok = ++previewTok;
      if (typeof view.inst.setTable === 'function') safe(function () { view.inst.setTable(table0); });
      var p = null;
      try { p = view.inst.play(spin); }
      catch (e) { console.error('[craps] preview failed:', e); previewDone(tok); return; }
      if (p && typeof p.then === 'function') p.then(function () { previewDone(tok); }, function () { previewDone(tok); });
      previewTimer = setTimeout(function () { previewDone(tok); }, dur + 250);
    };

    // ---- actions ----
    function snapshot() {
      var o = pick(w, EDIT_KEYS);
      o.x = Math.round(w.x * 100) / 100;
      o.y = Math.round(w.y * 100) / 100;
      o.scale = Math.round(w.scale * 100) / 100;
      o.sfx_volume = Math.round(w.sfx_volume * 100) / 100;
      return o;
    }
    function onKey(e) { if (e.key === 'Escape') { e.preventDefault(); close(); } }
    function onWinResize() { fitView(view); }
    if (!view.ro) window.addEventListener('resize', onWinResize);
    function close() {
      stopPreview();
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onWinResize);
      if (view.ro) view.ro.disconnect();
      if (view.inst) { var inst = view.inst; view.inst = null; safe(function () { inst.destroy(); }); }
      ov.remove();
      ED = null;
    }
    document.addEventListener('keydown', onKey);
    // The buttons are looked up inside the modal's chrome: the renderer's DOM in the preview
    // comes first in the modal and may use the same class names.
    q('.modal-header .close').onclick = close;
    q('.modal-actions .cancel').onclick = close;
    closeOnBackdrop(ov, close);

    q('.modal-actions .test').onclick = async function () {
      var d = await api('POST', API + '/roll', { test: true, overrides: snapshot() }, "Can't test");
      if (d) toast('fired to OBS — not saved yet');
    };
    q('.modal-actions .save').onclick = async function () {
      var d = await api('POST', '/games/api/config', { craps: snapshot() }, "Can't save");
      if (!d) return;
      var conf = d.config || (d.craps ? { craps: d.craps } : null);
      if (conf && isObj(conf.craps)) onConfig(conf);
      else { CFG_RAW = Object.assign({}, CFG_RAW || {}, snapshot()); mirrorConfig(); }
      toast('saved craps placement');
      // Modal stays open so you can keep fine-tuning. Close via × / Cancel / Esc / click-outside.
    };
    q('.modal-actions .reset').onclick = function () {
      Object.assign(w, pick(DEFAULTS, EDIT_KEYS));
      syncControls(); update(); pushCfg();
      toast('defaults restored — not saved yet');
    };

    ED = { refresh: pushCfg, close: close };
  }

  // ======================================================================
  // socket hooks (dispatched by games_panel.html)
  // ======================================================================
  function onState(st) {
    if (!isObj(st)) return;
    if (BACKEND !== true) noBackend(false);
    STATE = st; STATE_AT = performance.now();
    if (isObj(st.table)) { TABLE = st.table; TABLE_AT = STATE_AT; }
    mirrorState(st);
    renderReadout();
    renderFacts();
    renderTableAll(false);
    renderLimits();
    renderLast();
    renderRolls();
    maybeFetchStats(st);
    statsFollowTable();
    renderLedgerSoon();
    renderDisplay();
  }
  var LINK = null;
  function onLink(on) {
    var p = $('cr-ws-pill');
    p.textContent = on ? 'live' : 'offline — reconnecting';
    p.className = 'pill ' + (on ? 'on' : 'off');
    if (on && LINK === false && _ledgerBooted) fetchLedger(LAST_SEQ);   // catch up on whatever we missed while offline
    LINK = !!on;
  }
  function onTab(key) {
    if (key !== 'craps') return;
    if (MIR && !window.ResizeObserver) fitView(MIR);
    renderLedgerSoon();
  }

  // ======================================================================
  // Craps is being uninstalled
  // ======================================================================
  // The page removes the tab right after this: stop everything that would keep running against it.
  var TIMERS = [];   // setInterval handles
  function teardown() {
    TIMERS.forEach(function (t) { clearInterval(t); });
    TIMERS = [];
    [_confirmT, _vT, _statsT].forEach(function (t) { clearTimeout(t); });
    if (ED && ED.close) ED.close();
    if (MIR) {
      if (MIR.ro) MIR.ro.disconnect();
      if (MIR.inst) { var inst = MIR.inst; MIR.inst = null; safe(function () { inst.destroy(); }); }
    }
    var st = document.getElementById('cr-style');
    if (st) st.remove();
    delete window.CrapsPanel;
  }

  // ======================================================================
  // wiring
  // ======================================================================
  function init() {
    var st = document.createElement('style');
    st.id = 'cr-style';
    st.textContent = CSS;
    document.head.appendChild(st);
    buildMarkup();

    var overlayUrl = location.protocol + '//' + location.host + '/games/overlay';
    var gameUrl = overlayUrl + '?game=craps';
    $('cr-u-all').textContent = overlayUrl; $('cr-u-all').href = overlayUrl;
    $('cr-u-game').textContent = gameUrl; $('cr-u-game').href = gameUrl;
    $('cr-copy-all').addEventListener('click', function () { copyText(overlayUrl); });
    $('cr-copy-game').addEventListener('click', function () { copyText(gameUrl); });

    // table card
    $('cr-roll').addEventListener('click', async function () {
      var u = cleanUser($('cr-shooter').value);
      var d = await api('POST', API + '/roll', u ? { user: u } : {}, "Can't roll");
      if (d) toast('Dice are out — they land in ' + Math.round((+d.duration_ms || num(cfgFull().roll_seconds, 4) * 1000) / 1000) + 's');
    });
    $('cr-shooter').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); $('cr-roll').click(); } });
    $('cr-show').addEventListener('click', async function () { if (await api('POST', API + '/show', undefined, "Can't show")) toast('Table shown'); });
    $('cr-hide').addEventListener('click', async function () { if (await api('POST', API + '/hide', undefined, "Can't hide")) toast('Table hidden'); });
    $('cr-timer').addEventListener('click', startTimer);
    $('cr-timer-cancel').addEventListener('click', cancelTimer);
    $('cr-edit-place').addEventListener('click', function (e) { e.stopPropagation(); openEditor(); });
    $('cr-card-table').addEventListener('click', function (e) {
      if (!editMode()) return;
      if (e.target.closest('button, a, input, select, textarea, label, .pill')) return;
      openEditor();
    });

    // on the table: take-down / confirm (event delegation survives re-renders)
    $('cr-card-bets').addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b || b.disabled) return;
      if (b.dataset.bet) askConfirm('bet:' + b.dataset.bet);
      else if (b.dataset.odds) askConfirm('odds:' + b.dataset.odds);
      else if (b.dataset.user) askConfirm('user:' + b.dataset.user);
      else if (b.id === 'cr-clear') askConfirm('clear');
      else if (b.dataset.yes) takeDown(b.dataset.yes);
      else if (b.dataset.no) cancelConfirm();
    });

    // place a bet
    function soon() { if ($('cr-b-bet').value.trim() || $('cr-b-target').value) { clearTimeout(_vT); _vT = setTimeout(checkBet, 350); } }
    $('cr-b-bet').addEventListener('input', function () { clearTimeout(_vT); _vT = setTimeout(checkBet, 350); });
    $('cr-b-bet').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); checkBet(); } });
    $('cr-b-amount').addEventListener('input', soon);
    $('cr-b-user').addEventListener('input', function () { renderTargets(); soon(); });
    $('cr-b-target').addEventListener('change', soon);
    $('cr-b-check').addEventListener('click', checkBet);
    $('cr-b-place').addEventListener('click', placeBet);
    $('cr-b-examples').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-bet]');
      if (!b) return;
      $('cr-b-bet').value = b.dataset.bet;
      checkBet();
    });

    // Hex display
    if (D) {
      $('cr-d-lines').addEventListener('input', renderDCounts);
      $('cr-d-board').addEventListener('input', renderDCounts);
      $('cr-d-announce').addEventListener('click', dAnnounce);
      $('cr-d-clear').addEventListener('click', dClear);
      $('cr-d-setboard').addEventListener('click', dSetBoard);
      $('cr-d-clearboard').addEventListener('click', dClearBoard);
      renderDCounts();   // a reload can restore the boxes' text
    } else {
      ['cr-d-announce', 'cr-d-clear', 'cr-d-setboard', 'cr-d-clearboard'].forEach(function (id) { $(id).disabled = true; });
      $('cr-d-out').innerHTML = '<span class="bad">✕</span> This page is older than the craps tab — reload it (Ctrl+F5).';
    }

    // ledger
    $('cr-l-user').addEventListener('input', renderLedgerSoon);
    $('cr-l-all').addEventListener('click', function () { $('cr-l-user').value = ''; renderLedgerSoon(); });
    $('cr-l-reload').addEventListener('click', function () {
      LEDGER = []; SEEN = {}; LAST_SEQ = 0; FRESH = {}; LTRUNC = false; LSTATUS = 'loading';
      renderLedger(); fetchLedger(0);
    });

    // history
    $('cr-h-clear').addEventListener('click', async function () {
      if (!confirm('Clear the craps roll history and stats? (Bets on the table and the ledger are not touched.)')) return;
      if (await api('POST', API + '/history/clear')) { toast('History cleared'); fetchStats(); }
    });

    // settings
    SET.forEach(function (f) {
      var el = $('cr-s-' + f.k); if (!el) return;
      var mark = function () { DIRTY[f.k] = true; paintDirty(); };
      el.addEventListener('input', mark);
      el.addEventListener('change', mark);
      el.addEventListener('blur', function () { if (!DIRTY[f.k]) fillOne(f, cfgFull()); });
    });
    $('cr-s-sfx_volume').addEventListener('input', paintVol);
    $('cr-s-save').addEventListener('click', saveSettings);
    $('cr-s-revert').addEventListener('click', function () { DIRTY = {}; fillSettings(true); });

    MIR = makeView($('cr-mirror'), $('cr-mirror-stage'), $('cr-mirror-box'));
    if (!window.ResizeObserver) window.addEventListener('resize', function () { fitView(MIR); });

    buildApiCard();
    renderExamples();
    fillSettings(true);
    renderLimits();
    renderReadout();
    renderFacts();
    renderTableAll(true);
    renderStats([]);
    renderLedger();

    renderDisplay();

    TIMERS.push(setInterval(function () {
      if (!tabShown()) return;
      renderReadout();
      renderAuto();
      if (STATE && STATE.announce) renderDisplay();   // the card's countdown
    }, 200));

    // register with the page, then catch up on what its socket already delivered
    window.GamePanels = window.GamePanels || {};
    window.GamePanels.craps = { onConfig: onConfig, onState: onState, onStop: onStop, onLedger: onLedger, onLink: onLink, onTab: onTab, onRemove: teardown };
    var last = GP.last || {};
    if (last.config) onConfig(last.config);
    if (last.states && last.states.craps) onState(last.states.craps);
    if (last.link != null) onLink(last.link);
    // the ledger is read once the server's config shows it has craps (noBackend(false) -> bootLedger)
    // debugging / tests
    window.CrapsPanel = { openEditor: openEditor,
      state: function () { return { STATE: STATE, TABLE: TABLE, LEDGER: LEDGER, CFG: cfgFull(), STATS_SRC: STATS_SRC }; },
      computeStats: computeStats, fromServerStats: fromServerStats, localResult: localResult };
  }

  init();
})();
