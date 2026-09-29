/*
 * Hexcast Games — Roulette panel tab                     roulette_panel.js
 *
 * Builds the whole Roulette tab of the Games panel (/games -> section#tab-roulette):
 *   1. OBS browser source      both overlay URLs + Copy
 *   2. Roulette                live mirror (the real renderer, sound off), Spin / Show / Hide,
 *                              spin timer, state + countdown, last result; Edit Mode -> click ->
 *                              placement & look editor
 *   3. On the table            every bet riding on the next spin, take-down (confirm), refund all
 *   4. Place a bet             validate (dry run) + place (REAL: writes ledger debits) + spin with a bet
 *   5. Hex display             "Hex does the math": Hex's winners card (/announce) and board (/board)
 *   6. Ledger                  live tail (panel socket) + GET /ledger, per-user + house net
 *                              (roulette events only: the games ledger is shared with craps)
 *   7. History & stats         last 20 results, colour / odd-even / low-high bars, hot / cold, streak
 *   8. Settings                timing, the table's money rules, captions, sounds
 *   9. API                     endpoints, bet syntax, curl examples, hooking up the bank
 *
 * Plain browser script, no modules / deps / build step. games_panel.html loads it right after the
 * roulette.js renderer (window.HexGames.roulette) when Roulette is installed. Everything lives inside
 * one closure; what the page shares (toast, api, rawReq, the Hex display helpers ...) comes from
 * window.GamesPage, and the renderer's URL from GamesPage.gameScript('roulette').
 *
 * The page dispatches its /games/ws/panel socket to us through window.GamePanels.roulette:
 *   onConfig(config)  {"type":"config"}   -> config.roulette
 *   onState(state)    {"type":"state","game":"roulette"}   (STATE carries `table`)
 *   onStop(game)      {"type":"stop"[,"game"]}
 *   onLedger(events)  {"type":"ledger","game":"roulette","events":[...]}
 *   onLink(bool)      socket up / down
 *   onTab(key)        a game tab was selected
 *   onRemove()        Roulette is being uninstalled: stop the timers, close the editor, free the renderer
 * and keeps the last config / state / link in window.GamesPage.last so we can catch up.
 */
(function () {
var HOST = document.getElementById('tab-roulette');
if(!HOST) return;

var GP = window.GamesPage || {};
// What the page shares (panel_common.js): toast, copy, JSON helpers and the Hex display helpers.
var esc = GP.esc, toast = GP.toast, copyText = GP.copyText, api = GP.api, rawReq = GP.rawReq, busyToast = GP.busyToast;
var HD = GP.display;
var parseDisplayLines = HD.parseLines, countText = HD.countText, refuseText = HD.refuseText, displayRefuse = HD.refuse,
    displayReq = HD.req, displayError = HD.error, displayOut = HD.out, annSummary = HD.summary, annLeft = HD.left, annLive = HD.live;

// Looked up inside our own section, not the whole document: once Roulette is removed the section is
// detached, and a late timer or fetch reply must still find its elements instead of throwing.
var $ = function(id){ return HOST.querySelector('#' + id); };
var TIMERS = [];   // setInterval handles, cleared by onRemove()

/* ---------- constants (spec §2 / §4) — American double-zero wheel only ---------- */
var DEFAULTS = {
  x:50, y:50, scale:1.25, theme:'classic',
  red_color:'', black_color:'', green_color:'',
  spin_seconds:9, result_seconds:6, hide_when_idle:true,
  show_result:true, result_position:'center', result_details:true,
  show_history:true, history_count:10, show_user:true, show_bets:true, bets_max:5,
  sfx:true, sfx_volume:0.5, spin_clip:'', land_clip:'', cooldown_seconds:0,
  // the table + spin timer (ROULETTE_TIMER_SPEC §1)
  currency:'hexcoins', min_bet:1, max_bet:100000, auto_spin:false, bet_window_seconds:20,
  show_when_bets:true, show_table:true, table_max:6, table_position:'right', show_rules:true
};
// Keys the spin `overrides` may carry (spec §4 APPEARANCE).
var APPEARANCE = ['x','y','scale','theme','red_color','black_color','green_color','result_position',
  'result_details','show_result','show_history','history_count','show_user','show_bets','bets_max','sfx','sfx_volume',
  'show_table','table_max','table_position','show_rules'];
// Keys the placement modal edits (and saves / sends as Test-in-OBS overrides).
var EDIT_KEYS = ['x','y','scale','theme','red_color','black_color','green_color','result_position',
  'result_details','show_result','show_history','history_count','show_table','table_max','table_position','show_rules'];
var THEMES = [['classic','Classic — walnut & gold'],['neon','Neon'],['midnight','Midnight'],['royal','Royal']];
var TABLE_POSITIONS = [['right','Right of the wheel'],['left','Left of the wheel'],['below','Below the wheel'],['above','Above the wheel']];
// Ledger reasons (the roulette events of the shared games ledger).
var REASONS = { bet:'bet', add:'added to bet', win:'win', remove:'taken down', refund:'refund · table cleared' };
var LEDGER_MAX = 10000, LEDGER_PAGE = 5000, LEDGER_ROWS = 300;
var CLASSIC = { red:'#c0282d', black:'#16161a', green:'#0f7a3c' };
var RED_NUMS = {1:1,3:1,5:1,7:1,9:1,12:1,14:1,16:1,18:1,19:1,21:1,23:1,25:1,27:1,30:1,32:1,34:1,36:1};
// Wheel order (spec §2). Only a fallback: the renderer's HexGames.roulette.AMERICAN is preferred.
var AMERICAN = ["0","28","9","26","30","11","7","20","32","17","5","22","34","15","3","24","36","13",
                "1","00","27","10","25","29","12","8","19","31","18","6","21","33","16","4","23","35","14","2"];
// Every pocket label in table order (0, 00, 1..36) — for local stats.
var POCKETS = ['0', '00'];
for(var _n = 1; _n <= 36; _n++) POCKETS.push(String(_n));

/* ---------- helpers ---------- */
function clamp(v, lo, hi){ return Math.max(lo, Math.min(hi, v)); }
function num(v, d){ v = parseFloat(v); return isFinite(v) ? v : d; }
function pick(o, keys){ var r = {}; keys.forEach(function(k){ if(o && o[k] !== undefined) r[k] = o[k]; }); return r; }
function isObj(o){ return !!o && typeof o === 'object' && !Array.isArray(o); }
// User names are chat input: "constructor" or "__proto__" must never hit Object.prototype.
function dict(){ return Object.create(null); }
function own(o, k){ return !!o && Object.prototype.hasOwnProperty.call(o, k); }
function plural(n, w){ return n + ' ' + w + (n === 1 ? '' : 's'); }
// Same cleaning as the server's _clean_user(): trim, drop leading @, trim, 40 chars (case kept).
function cleanUser(v){ return String(v == null ? '' : v).trim().replace(/^@+/, '').trim().slice(0, 40); }
function noop(){}
function safe(fn){ try{ return fn(); }catch(e){ console.error('[games] renderer error:', e); } }
function R(){ return (window.HexGames && window.HexGames.roulette) || null; }
function baseSize(){ var r = R(); return (r && +r.BASE_SIZE) || 400; }
function colorOf(label){
  label = String(label);
  if(label === '0' || label === '00') return 'green';
  return RED_NUMS[parseInt(label, 10)] ? 'red' : 'black';
}
function detailsOf(res){
  if(!res) return '';
  if(res.color === 'green' || res.parity == null) return res.number === '00' ? 'DOUBLE ZERO' : 'ZERO';
  var parts = [];
  if(res.parity) parts.push(String(res.parity).toUpperCase());
  if(res.range) parts.push(String(res.range).toUpperCase());
  if(res.dozen) parts.push((['1ST','2ND','3RD'][res.dozen - 1] || res.dozen) + ' 12');
  return parts.join(' · ');
}
function fmtSec(ms){
  ms = Math.max(0, ms);
  if(ms >= 60000){ var s = Math.ceil(ms/1000); return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0'); }
  return (ms/1000).toFixed(1) + 's';
}
function fmtClock(ms){ var s = Math.max(0, Math.ceil(ms/1000)); return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0'); }
function fmtAmt(v){ v = +v || 0; return (Math.round(v*100)/100).toLocaleString(); }
function signedAmt(v){ v = Math.round(+v || 0); return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toLocaleString(); }
function cur(){ var c = (TABLE && TABLE.currency) || cfgFull().currency; return String(c || 'hexcoins'); }
function coins(v){ return fmtAmt(v) + ' ' + cur(); }
function pkChip(label, cls, title){
  var c = colorOf(label);
  return '<span class="pk ' + c + (cls ? ' ' + cls : '') + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(label) + '</span>';
}

/* ---------- HTTP (spec §5) ---------- */
// api() / rawReq() / busyToast() are the page's (window.GamesPage).
// A table / timer / ledger route the server doesn't have = a Hexcast from before the roulette table.
function tableErr(res){
  var d = res.d || {};
  if(res.status === 404 || res.status === 405 || d.error === 'unknown game'){
    tableSupport(false);
    return 'This Hexcast has no roulette table or spin timer yet — restart it after updating';
  }
  return (typeof d.error === 'string' && d.error) || (typeof d.detail === 'string' && d.detail) || ('Request failed (HTTP ' + res.status + ')');
}
// Like api() for the table / timer routes: toasts on failure, returns the body or null.
async function tableApi(method, path, body, verb){
  var res = await rawReq(method, path, body);
  if(res.net){ toast('Network error — is Hexcast running?', 2400); return null; }
  if(res.status === 409 || res.d.error === 'busy' || res.d.error === 'bets_closed'){ busyToast(res.d, verb); return null; }
  if(!res.ok){ toast(tableErr(res), 2600); return null; }
  return res.d;
}

/* ---------- config ---------- */
var CFG_RAW = null;   // server's config.roulette (null until the first config message)
function cfgFull(){
  var c = Object.assign({}, DEFAULTS, CFG_RAW || {});
  delete c.wheel;   // American wheel only: a legacy `wheel` key from an old config is ignored
  return c;
}
function setPocketVars(c){
  var s = document.documentElement.style;
  s.setProperty('--pk-red',   c.red_color   || themeColor(c.theme, 'red'));
  s.setProperty('--pk-black', c.black_color || themeColor(c.theme, 'black'));
  s.setProperty('--pk-green', c.green_color || themeColor(c.theme, 'green'));
}
function onConfig(config){
  var rc = config && config.roulette;
  if(!rc || typeof rc !== 'object') return;
  CFG_RAW = rc;
  // A server with the table sends its keys; one without them (and no STATE.table) is older.
  if(own(rc, 'bet_window_seconds')) tableSupport(true);
  else if(!(STATE && isObj(STATE.table))) tableSupport(false);
  setPocketVars(cfgFull());
  fillSettings(false);
  if(ensureMirror()) mirrorConfig();
  if(ED) ED.refresh();
  renderFacts();
  renderLimits();
  renderTableCard(true);
}
var TBL = null;   // true / false once known: does this Hexcast have the roulette table (+ spin timer, ledger)?
function tableSupport(on){
  on = !!on;
  if(TBL === on) return;
  TBL = on;
  $('rt-banner').classList.toggle('on', !on);
  if(on) bootLedger();   // read the ledger once; the socket keeps it live
  else if(LSTATUS === 'loading' && !LEDGER.length){ LSTATUS = 'unavailable'; renderLedgerSoon(); }
  renderTableCard(true);
  renderLimits();
}

/* ---------- stage views (a 1920x1080 stage scaled into a 16:9 box) ---------- */
function makeView(box, stage, wheel){
  var v = { box: box, stage: stage, wheel: wheel, inst: null, _rz: false, ro: null };
  var bs = baseSize();
  wheel.style.width = bs + 'px'; wheel.style.height = bs + 'px';
  fitView(v);
  if(window.ResizeObserver){ v.ro = new ResizeObserver(function(){ fitView(v); }); v.ro.observe(box); }
  return v;
}
function fitView(v){
  var w = v.box.clientWidth;
  if(!w) return;
  v.stage.style.transform = 'scale(' + (w / 1920) + ')';
  requestResize(v);
}
function placeView(v, x, y, scale){
  v.wheel.style.left = x + '%';
  v.wheel.style.top = y + '%';
  v.wheel.style.transform = 'translate(-50%,-50%) scale(' + scale + ')';
  requestResize(v);
}
// resize() after any change, coalesced to one call per frame.
function requestResize(v){
  if(v._rz) return;
  v._rz = true;
  requestAnimationFrame(function(){ v._rz = false; if(v.inst) safe(function(){ v.inst.resize(); }); });
}
function viewMsg(el, text){ el.textContent = text || ''; el.style.display = text ? 'flex' : 'none'; }
// An editor modal closes on a click on its backdrop only when the press started there too:
// selecting text in an input and letting go outside the modal is not a click outside.
function closeOnBackdrop(ov, close){
  var downOnOv = false;
  ov.addEventListener('pointerdown', function(e){ downOnOv = e.target === ov; });
  ov.addEventListener('click', function(e){ var d = downOnOv; downOnOv = false; if(e.target === ov && d) close(); });
}

/* ---------- live mirror (driven by WS state, like the overlay §8) ---------- */
var STATE = null, STATE_AT = 0;
var TABLE = null, TABLE_AT = 0;   // the roulette TABLE (STATE.table or a /bet · /remove · /timer … reply)
var MIR = null;
var MIRS = { id: null, cfgKey: '', hist: null, table: null, ann: null, idled: false, failed: false };
function ensureMirror(){
  if(MIR && MIR.inst) return true;
  if(!MIR || MIRS.failed) return false;
  var r = R();
  if(!r || typeof r.create !== 'function'){
    MIRS.failed = true;
    MIR.wheel.classList.add('empty');
    viewMsg($('mirror-msg'), 'Wheel renderer not loaded (' + (GP.gameScript('roulette') || 'roulette.js') + ').');
    return false;
  }
  var eff = mirrorEffective();
  try{ MIR.inst = r.create(MIR.wheel, eff, { sound: false }); }
  catch(e){
    console.error('[games] renderer create failed:', e);
    MIRS.failed = true;
    viewMsg($('mirror-msg'), 'Wheel renderer failed to start: ' + (e && e.message || e));
    return false;
  }
  MIRS.cfgKey = JSON.stringify(eff);
  placeView(MIR, num(eff.x, 50), num(eff.y, 50), num(eff.scale, 1.25));
  return true;
}
// Saved config, plus the current spin's appearance overrides while it is on screen.
function mirrorEffective(){
  var c = cfgFull();
  var sp = STATE && STATE.spin;
  if(sp && sp.overrides && typeof sp.overrides === 'object') Object.assign(c, pick(sp.overrides, APPEARANCE));
  return c;
}
function mirrorConfig(){
  if(!MIR || !MIR.inst) return;
  var eff = mirrorEffective();
  var key = JSON.stringify(eff);
  if(key !== MIRS.cfgKey){ MIRS.cfgKey = key; safe(function(){ MIR.inst.setConfig(eff); }); }
  placeView(MIR, num(eff.x, 50), num(eff.y, 50), num(eff.scale, 1.25));
}
function setMirrorVisible(on){
  if(!MIR) return;
  MIR.wheel.classList.toggle('off', !on);
  var t = $('mirror-tag');
  t.textContent = on ? 'on stream' : 'hidden';
  t.classList.toggle('on', !!on);
}
// The table as the renderer should see it now (the spin countdown aged since it arrived).
function liveTable(){
  if(!isObj(TABLE)) return null;
  var t = Object.assign({}, TABLE), dt = performance.now() - TABLE_AT;
  ['auto_spin_in_ms', 'auto_roll_in_ms'].forEach(function(k){
    if(typeof t[k] === 'number' && isFinite(t[k])) t[k] = Math.max(0, Math.round(t[k] - dt));
  });
  return t;
}
// setTable is optional (a renderer without it just has no board / countdown).
function mirrorTable(force){
  if(!MIR || !MIR.inst || typeof MIR.inst.setTable !== 'function' || !isObj(TABLE)) return;
  var key = JSON.stringify(TABLE);
  if(!force && key === MIRS.table) return;
  MIRS.table = key;
  var t = liveTable();
  safe(function(){ MIR.inst.setTable(t); });
}
function histKey(h){ return (h || []).map(function(r){ return r && r.number; }).join(','); }
function mirrorState(st){
  setMirrorVisible(!!st.visible);
  if(!ensureMirror()) return;
  mirrorConfig();
  var inst = MIR.inst, sp = st.spin;
  mirrorTable(false);   // table BEFORE play (the renderer holds it while the ball flies)
  var hk = histKey(st.history) + '|' + ((st.last && st.last.id) || '');
  if(hk !== MIRS.hist){ MIRS.hist = hk; safe(function(){ inst.setHistory(st.history || []); }); }
  var acted = false;   // play / showResult / reset: each one clears the renderer's Hex card
  if(sp && sp.id){
    if(sp.id !== MIRS.id){
      MIRS.id = sp.id; MIRS.idled = false; acted = true;
      var landed = st.state !== 'spinning' || (+sp.elapsed_ms || 0) >= (+sp.duration_ms || 0);
      if(landed) safe(function(){ inst.showResult(sp); });
      else safe(function(){ var p = inst.play(sp); if(p && typeof p.catch === 'function') p.catch(noop); });
    }
  } else if(MIRS.id !== null || !MIRS.idled){
    // No spin in the air or on screen (idle, or cooldown after the result phase):
    // back to the idle wheel, exactly when the overlay resets (§8 reconcile).
    MIRS.id = null; MIRS.idled = true; acted = true;
    safe(function(){ inst.reset(); });
    mirrorTable(true);   // whatever reset() cleared, the table is still there
  }
  mirrorAnnounce(inst, st.announce, acted);
}
// Hex's own winners card (STATE.announce), handed over like the overlay does: after
// play/showResult/reset (the renderer holds it while the ball rolls), only when it changed
// (expires_in_ms ticks don't count) or again after one of those cleared it. Optional method.
function annKey(a){
  if(!a || typeof a !== 'object' || Array.isArray(a)) return null;
  var o = {};
  Object.keys(a).sort().forEach(function(k){ if(k !== 'expires_in_ms') o[k] = a[k]; });
  return JSON.stringify(o);
}
function mirrorAnnounce(inst, a, acted){
  var k = annKey(a);
  if(k === MIRS.ann && !(k && acted)) return;
  MIRS.ann = k;
  if(typeof inst.setAnnounce === 'function') safe(function(){ inst.setAnnounce(k ? a : null); });
}
function onStop(){
  setMirrorVisible(false);
  MIRS.id = null; MIRS.idled = true; MIRS.ann = null;
  if(MIR && MIR.inst){ safe(function(){ MIR.inst.reset(); }); mirrorTable(true); }
}

/* ---------- state readout + last result ---------- */
function renderReadout(){
  var elR = $('g-readout'), elS = $('g-state'), elSub = $('g-sub');
  var st = STATE;
  if(!st){ elR.textContent = '—'; elS.textContent = 'offline'; elS.className = ''; elSub.textContent = ''; return; }
  var dt = performance.now() - STATE_AT;
  var s = st.state || 'idle', sp = st.spin;
  var busy = Math.max(0, (+st.busy_ms || 0) - dt);
  var phase = null, sub = '', label = s;
  if(s === 'spinning' && sp){
    phase = (+sp.duration_ms || 0) - (+sp.elapsed_ms || 0) - dt;
    sub = 'until the ball lands' + (busy > 0 ? ' · next spin in ' + fmtSec(busy) : '');
  } else if(s === 'result' && sp){
    var rm = (sp.result_ms != null) ? +sp.result_ms : num(cfgFull().result_seconds, 6) * 1000;
    phase = (+sp.duration_ms || 0) + rm - (+sp.elapsed_ms || 0) - dt;
    sub = 'result on screen' + (busy > 0 ? ' · next spin in ' + fmtSec(busy) : '');
  } else if(s === 'result' || s === 'cooldown'){
    phase = busy;
    sub = s === 'cooldown' ? 'cooling down — next spin when this hits 0' : 'result on screen';
  } else {
    var left = autoLeft();
    if(left != null){ phase = left; label = 'betting'; sub = 'spin timer — the wheel spins by itself when this hits 0'; }
    else sub = st.visible ? 'ready — wheel is showing' : 'ready to spin';
  }
  elR.textContent = (phase == null) ? '—' : (label === 'betting' ? fmtClock(phase) : fmtSec(phase));
  elS.textContent = label;
  elS.className = label;
  elSub.textContent = sub;
}
TIMERS.push(setInterval(renderReadout, 100));

/* ---------- spin timer + facts (TABLE.auto_spin_in_ms, spec §4) ---------- */
function autoLeft(){
  if(!TABLE || typeof TABLE.auto_spin_in_ms !== 'number' || !isFinite(TABLE.auto_spin_in_ms)) return null;
  return Math.max(0, TABLE.auto_spin_in_ms - (performance.now() - TABLE_AT));
}
function fact(id, html, cls, title){
  var el = $(id); if(!el) return;
  el.innerHTML = html;
  el.className = cls || '';
  el.title = title || '';
}
function renderFacts(){
  var t = TABLE;
  if(!t){
    var why = TBL === false ? 'This Hexcast has no roulette table yet' : '';
    fact('g-f-total', '—', 'dim', why); fact('g-f-open', '—', 'dim', why);
    renderTimer();
    return;
  }
  var n = Array.isArray(t.bets) ? t.bets.length : 0;
  fact('g-f-total', esc(fmtAmt(t.total_on_table)), n ? '' : 'dim', plural(n, 'bet') + ' · ' + coins(t.total_on_table));
  fact('g-f-open', t.bets_open === false ? 'closed' : 'open', t.bets_open === false ? 'warn' : 'good',
    t.bets_open === false ? 'Ball in flight — bets and take-downs wait for the landing' : 'Bets and take-downs are accepted');
  renderTimer();
}
function renderTimer(){
  var left = autoLeft(), c = cfgFull();
  if(left != null) fact('g-f-timer', 'in ' + fmtClock(left), 'good', 'Spin timer — the wheel spins by itself at 0, with whatever is on the table');
  else if(!TABLE) fact('g-f-timer', '—', 'dim', TBL === false ? 'This Hexcast has no spin timer yet' : '');
  else if(c.auto_spin) fact('g-f-timer', (TABLE.bets || []).length ? 'armed' : 'waiting', 'dim',
    'Auto-spin is on: a ' + c.bet_window_seconds + 's countdown starts when bets are down and the wheel is idle');
  else fact('g-f-timer', 'off', 'dim', 'Auto-spin is off — Start timer, or spin from here, a bot or chat');
}
async function startTimer(){
  var body = {}, raw = $('g-timer-secs').value.trim();
  if(raw !== ''){
    var v = parseFloat(raw);
    if(!isFinite(v)){ toast('Seconds must be a number (5–300)'); $('g-timer-secs').focus(); return; }
    body.seconds = clamp(v, 5, 300);
  }
  var d = await tableApi('POST', '/games/api/roulette/timer', body, "Can't start the timer");
  if(!d) return;
  if(isObj(d.table)) applyTable(d.table);
  var ms = typeof d.auto_in_ms === 'number' ? d.auto_in_ms : autoLeft();
  toast('Timer started — the wheel spins' + (ms != null ? ' in ' + fmtClock(ms) : ' when it runs out'), 2400);
}
async function cancelTimer(){
  var d = await tableApi('POST', '/games/api/roulette/timer/cancel', undefined, "Can't cancel the timer");
  if(!d) return;
  if(isObj(d.table)) applyTable(d.table);
  toast(d.cancelled === false ? 'No timer was running' : 'Timer cancelled — nothing spins');
}

function renderLast(st){
  var host = $('g-last');
  var sp = null;
  // The in-flight spin only once it has landed (never spoil it); otherwise the last committed one.
  if(st.spin && st.spin.result && st.state && st.state !== 'spinning' && st.state !== 'idle') sp = st.spin;
  else if(st.last && st.last.result) sp = st.last;
  if(!sp){ host.innerHTML = '<span class="none">No spins yet.</span>'; return; }
  var res = sp.result, c = res.color || colorOf(res.number);
  var who = [];
  if(sp.user) who.push('@' + esc(sp.user));
  var sm = sp.summary;
  if(sm && +sm.bets) who.push(esc(sm.bets) + ' bet' + (+sm.bets === 1 ? '' : 's') + ' · ' + esc(+sm.winners || 0) + ' won');
  host.innerHTML =
    '<span class="disc ' + esc(c) + '">' + esc(res.number) + '</span>' +
    '<span class="meta"><span class="det">' + esc(detailsOf(res)) + (sp.test ? '<span class="tagt">TEST</span>' : '') + '</span>' +
    (who.length ? '<span class="who">' + who.join(' · ') + '</span>' : '') + '</span>';
}

/* ---------- history & stats ---------- */
function renderHistory(list){
  $('h-chips').innerHTML = (list || []).slice(0, 20).map(function(r, i){
    if(!r) return '';
    var d = detailsOf(r);
    return pkChip(r.number, i === 0 ? 'first' : '', r.number + ' ' + (r.color || colorOf(r.number)) + (d ? ' · ' + d : ''));
  }).join('');
}
function localStats(list){
  var s = { spins: 0, red: 0, black: 0, green: 0, odd: 0, even: 0, low: 0, high: 0, counts: {}, hot: [], cold: [], streak: null };
  (list || []).forEach(function(r){
    if(!r) return;
    s.spins++;
    if(s[r.color] !== undefined) s[r.color]++;
    if(r.parity === 'odd') s.odd++; else if(r.parity === 'even') s.even++;
    if(r.range === 'low') s.low++; else if(r.range === 'high') s.high++;
    s.counts[r.number] = (s.counts[r.number] || 0) + 1;
  });
  var labels = POCKETS;
  var byCount = labels.slice().sort(function(a, b){ return (s.counts[b] || 0) - (s.counts[a] || 0); });
  s.hot = byCount.filter(function(l){ return s.counts[l]; }).slice(0, 5);
  s.cold = labels.slice().sort(function(a, b){ return (s.counts[a] || 0) - (s.counts[b] || 0); }).slice(0, 5);
  if(list && list.length && list[0]){
    var col = list[0].color, len = 0;
    for(var i = 0; i < list.length && list[i] && list[i].color === col; i++) len++;
    s.streak = { color: col, length: len };
  }
  return s;
}
function renderStats(s){
  var host = $('h-stats');
  s = s || {};
  var n = +s.spins || 0;
  $('h-spins').textContent = n ? (n + ' spin' + (n === 1 ? '' : 's') + ' counted') : '';
  if(!n){ host.innerHTML = ''; return; }
  function bar(label, parts){
    var total = parts.reduce(function(a, p){ return a + (+p[1] || 0); }, 0);
    var segs = parts.map(function(p){ var v = +p[1] || 0; return v ? '<i class="' + p[0] + '" style="flex:' + v + '"></i>' : ''; }).join('');
    var cnt = parts.map(function(p){
      var v = +p[1] || 0;
      return p[2] + ' ' + v + (total ? ' (' + Math.round(v * 100 / total) + '%)' : '');
    }).join(' · ');
    return '<div class="srow"><div class="top"><span class="lbl">' + label + '</span><span class="cnt">' + cnt + '</span></div>' +
           '<div class="bar">' + segs + '</div></div>';
  }
  var counts = s.counts || {};
  function chipList(arr){
    arr = arr || [];
    if(!arr.length) return '<span class="none">—</span>';
    return arr.map(function(l){ var c = +counts[l] || 0; return pkChip(l, 'sm', l + ' — seen ' + c + ' time' + (c === 1 ? '' : 's')); }).join('');
  }
  var streak = '<span class="none">—</span>';
  if(s.streak && s.streak.color && +s.streak.length){
    streak = '<span class="pk sm ' + esc(s.streak.color) + '" style="padding:0 9px">' + esc(s.streak.length) + '× ' + esc(String(s.streak.color).toUpperCase()) + '</span>';
  }
  host.innerHTML =
    bar('Colour', [['red', s.red, 'R'], ['black', s.black, 'B'], ['green', s.green, 'G']]) +
    bar('Odd / even', [['a', s.odd, 'odd'], ['b', s.even, 'even']]) +
    bar('Low / high', [['a', s.low, 'low'], ['b', s.high, 'high']]) +
    '<div class="hc">' +
      '<span class="lbl">Hot</span><span class="chips">' + chipList(s.hot) + '</span>' +
      '<span class="lbl">Cold</span><span class="chips">' + chipList(s.cold) + '</span>' +
      '<span class="lbl">Streak</span><span class="chips">' + streak + '</span>' +
    '</div>';
}
// Stats aren't in the WS state, so re-read them when the history actually changes (event-driven, no polling).
var _statsKey = null, _statsT = null, _statsSeq = 0;
function maybeFetchStats(st){
  var key = histKey(st.history) + '|' + ((st.last && st.last.id) || '');
  if(key === _statsKey) return;
  _statsKey = key;
  clearTimeout(_statsT);
  _statsT = setTimeout(fetchStats, 150);
}
async function fetchStats(){
  var seq = ++_statsSeq;   // a slow, older reply must not overwrite a newer one
  try{
    var r = await fetch('/games/api/roulette/history?limit=20');
    var d = await r.json();
    if(seq !== _statsSeq) return;
    if(r.ok && d && d.ok !== false && d.stats){ renderStats(d.stats); return; }
  }catch(e){
    if(seq !== _statsSeq) return;
  }
  renderStats(localStats(STATE && STATE.history));
}

/* ---------- bet tester ---------- */
var EXAMPLES = [
  'red', 'black', 'odd', 'low', '17', '0', '00',
  'split:17/20', '0/00', 'street:13', '0/00/2', 'corner:17', 'basket', 'line:13',
  'dozen2', 'col3'
];
function renderExamples(){
  $('b-examples').innerHTML = EXAMPLES.map(function(e){
    return '<button type="button" data-bet="' + esc(e) + '">' + esc(e) + '</button>';
  }).join('');
}
var _vSeq = 0, _vT = null, LASTV = null;   // LASTV: the last check, re-drawn when the table / user / amount change
function renderBetOut(d){
  var host = $('b-out');
  LASTV = d || null;
  if(!d){ host.innerHTML = '<span class="none">Type a bet (or pick an example) to see how it resolves.</span>'; return; }
  if(d.ok === false && d.valid === undefined){ host.innerHTML = '<span class="bad">✕</span> ' + esc(d.error || 'could not check that bet'); return; }
  if(!d.valid){ host.innerHTML = '<span class="bad">✕ Invalid</span> — ' + esc(d.error || 'not a recognised bet') + '<div class="note" style="margin-top:4px">Placed, it is rejected and nothing is debited; with a spin, it would be refunded, not lost.</div>'; return; }
  var amt = parseFloat($('b-amount').value);
  var odds = +d.odds || 0;
  var pay = (isFinite(amt) && amt > 0)
    ? ' · a win pays <b>+' + esc(fmtAmt(amt * odds)) + '</b> (returns ' + esc(fmtAmt(amt * (odds + 1))) + ')' : '';
  host.innerHTML =
    '<span class="ok">✓ ' + esc(d.label || d.type) + '</span> <span class="note">(' + esc(d.type) + ')</span> · ' +
    '<span class="odds">' + esc(odds) + ' to 1</span>' + pay +
    '<div class="chips">' + (d.numbers || []).map(function(l){ return pkChip(l, 'sm'); }).join('') + '</div>' + placeNote(d);
}
// What Place would do with this bet on the table as it is now (the server has the last word).
function sameLine(user, d){
  var key = function(nums){ return (nums || []).map(String).sort().join(','); }, want = key(d.numbers);
  return ((TABLE && TABLE.bets) || []).filter(function(b){
    return b && String(b.user) === user && b.type === d.type && key(b.numbers) === want;
  })[0] || null;
}
function placeNote(d){
  if(!TBL || !d || !d.valid) return '';
  var t = TABLE || {}, c = cfgFull();
  var min = t.min_bet != null ? +t.min_bet : +c.min_bet, max = t.max_bet != null ? +t.max_bet : +c.max_bet;
  var amt = parseFloat($('b-amount').value), user = cleanUser($('b-user').value);
  var line = user ? sameLine(user, d) : null, warn = [];
  if(isFinite(amt)){
    if(amt !== Math.floor(amt) || amt < 1) warn.push('Place takes whole ' + cur() + ' (1 or more)');
    else if(amt < min) warn.push('below the table minimum (' + fmtAmt(min) + ')');
    else if(max > 0 && amt + (line ? +line.amount || 0 : 0) > max) warn.push('over the table maximum (' + fmtAmt(max) + (line ? ' for the whole line' : '') + ')');
  }
  return (line ? '<div class="note" style="margin-top:6px">Place adds to @' + esc(user) + '\'s ' + esc(line.label || line.bet) +
      ' already down (' + esc(fmtAmt(line.amount)) + ')</div>' : '') +
    (warn.length ? '<div class="note" style="margin-top:6px;color:var(--warn)">' + warn.map(esc).join(' · ') + '</div>' : '');
}
function renderLimits(){
  var el = $('b-limits'), c = cfgFull(), t = TABLE || {};
  if(TBL === false){ el.textContent = 'Place needs the roulette table — this Hexcast doesn\'t have it yet.'; return; }
  var min = t.min_bet != null ? t.min_bet : c.min_bet, max = t.max_bet != null ? t.max_bet : c.max_bet;
  el.textContent = 'Place: min ' + fmtAmt(min) + ' · max ' + (+max ? fmtAmt(max) : 'none') + ' per bet line · whole ' + cur() +
    ' · the same bet again adds to that line · a win returns the stake + odds × stake';
}
async function placeBet(){
  var user = cleanUser($('b-user').value), bet = $('b-bet').value.trim(), raw = $('b-amount').value.trim();
  if(!user){ toast('Who is betting? Fill in the user'); $('b-user').focus(); return; }
  if(!bet){ toast('Type a bet first'); $('b-bet').focus(); return; }
  var amt = Number(raw);
  if(raw === '' || !isFinite(amt) || amt < 1 || Math.floor(amt) !== amt){
    toast('Amount must be a whole number of ' + cur()); $('b-amount').focus(); return;
  }
  var body = { user: user, bet: bet, amount: amt }, btn = $('b-place');
  btn.disabled = true;
  var res = await rawReq('POST', '/games/api/roulette/bet', body);
  btn.disabled = false;
  if(res.net){ toast('Network error — is Hexcast running?', 2400); return; }
  if(res.status === 409 || res.d.error === 'busy' || res.d.error === 'bets_closed'){ busyToast(res.d, "Can't bet"); return; }
  var d = res.d;
  if(!Array.isArray(d.accepted) && !Array.isArray(d.rejected)){
    if(!res.ok) toast(tableErr(res), 2600);
    return;
  }
  renderPlaced(d);
  if(isObj(d.table)) applyTable(d.table);
  if((d.accepted || []).length){
    var deb = (d.debits || []).reduce(function(a, x){ return a + (+x.amount || 0); }, 0);
    toast('Bet placed — debit ' + coins(deb) + ' from @' + user, 2600);
  } else toast('Bet rejected — nothing was debited', 2600);
}
function renderPlaced(d){
  var debits = dict();
  (d.debits || []).forEach(function(x){ if(x && x.bet_id != null) (debits[x.bet_id] = debits[x.bet_id] || []).push(x); });
  var rows = (d.accepted || []).map(function(b){
    var ds = own(debits, b.id) ? debits[b.id] : [];
    var deb = ds.map(function(x){ return '<span class="seq">#' + esc(x.seq) + '</span> −' + esc(fmtAmt(x.amount)); }).join(', ');
    var pays = b.pays != null ? +b.pays : (+b.amount || 0) * ((+b.odds || 0) + 1);
    return '<div class="bres win"><b>ACCEPTED</b>' + esc(b.label || b.bet || b.type) +
      ' <span class="u">@' + esc(b.user) + '</span> · <span class="m">' + esc(fmtAmt(b.amount)) + '</span>' +
      (b.action === 'add' ? ' <span class="u">(added' + (b.added != null ? ' ' + esc(fmtAmt(b.added)) : '') + ' to the bet already down)</span>' : '') +
      ' · a win returns ' + esc(fmtAmt(pays)) + (deb ? ' · debit ' + deb : '') + '</div>';
  });
  (d.rejected || []).forEach(function(r){
    if(!r) return;
    rows.push('<div class="bres lose"><b>REJECTED</b>' + esc(r.bet) + (r.amount != null ? ' · <span class="m">' + esc(r.amount) + '</span>' : '') +
      (r.user ? ' <span class="u">@' + esc(r.user) + '</span>' : '') + ' <span class="e">— ' + esc(r.error || 'rejected') +
      (r.hint ? ' (' + esc(r.hint) + ')' : '') + '</span></div>');
  });
  $('b-placed').innerHTML = rows.join('') || '<div class="note" style="margin-top:10px">Nothing was sent.</div>';
}

/* ---------- on the table (bets riding on the next spin; take-down, refund all) ---------- */
// Inline confirm (like the craps tab): the question replaces the button for 8 s.
var CONFIRM = null, _confirmT = null;
function askConfirm(key){
  CONFIRM = { key: key };
  clearTimeout(_confirmT);
  _confirmT = setTimeout(function(){ CONFIRM = null; renderTableCard(true); }, 8000);
  renderTableCard(true);
}
function cancelConfirm(){ CONFIRM = null; clearTimeout(_confirmT); renderTableCard(true); }
function confirming(key){ return !!CONFIRM && CONFIRM.key === key; }
function confirmHTML(key, q, yes){
  return '<span class="confirm"><span class="q">' + q + '</span>' +
    '<button class="mini yes" data-yes="' + esc(key) + '">' + esc(yes) + '</button>' +
    '<button class="mini" data-no="1">Keep</button></span>';
}
function betsByUser(bets){
  var map = dict(), order = [];
  (bets || []).forEach(function(b){
    if(!b) return;
    var u = String(b.user || '—');
    if(!map[u]){ map[u] = []; order.push(u); }
    map[u].push(b);
  });
  var exp = (TABLE && TABLE.exposure) || {};
  function expOf(u){ return own(exp, u) && exp[u] != null ? +exp[u] : map[u].reduce(function(a, b){ return a + (+b.amount || 0); }, 0); }
  order.sort(function(a, b){ return expOf(b) - expOf(a) || a.localeCompare(b); });
  return order.map(function(u){ return { user: u, bets: map[u], exposure: expOf(u) }; });
}
var _tableKey = null;
function renderTableCard(force){
  var key = JSON.stringify([TABLE && TABLE.bets, TABLE && TABLE.bets_open, TABLE && TABLE.exposure, cur(), CONFIRM && CONFIRM.key, TBL]);
  if(!force && key === _tableKey) return;
  _tableKey = key;
  var host = $('t-bets'), t = TABLE, bets = (t && Array.isArray(t.bets)) ? t.bets : [];
  var open = !t || t.bets_open !== false;
  var dis = open ? '' : ' disabled title="Frozen while the ball is rolling"';
  $('t-pill').textContent = bets.length ? plural(bets.length, 'bet') : 'no bets';
  $('t-pill').className = 'pill' + (bets.length ? ' on' : '');
  if(!bets.length){
    host.innerHTML = '<tbody><tr><td class="tempty">' + (t ? 'Nothing on the table. Bets placed by chat, a bot or the form next door show up here.'
      : TBL === false ? 'This Hexcast has no roulette table yet — restart it after updating.' : 'Waiting for the table…') + '</td></tr></tbody>';
    $('t-total').textContent = '';
    $('t-clear-wrap').innerHTML = '';
    return;
  }
  var groups = betsByUser(bets);
  var rows = ['<thead><tr><th>Bet</th><th class="num">Amount</th><th class="num" title="What a win returns, stake included">Pays</th><th></th></tr></thead><tbody>'];
  groups.forEach(function(g){
    var ukey = 'user:' + g.user, uact = '';
    var downs = g.bets.filter(function(b){ return b.removable !== false; });
    if(downs.length >= 2){
      var back = downs.reduce(function(a, b){ return a + (+b.amount || 0); }, 0);
      uact = confirming(ukey)
        ? confirmHTML(ukey, 'Take down all ' + downs.length + ' of @' + esc(g.user) + '\'s bets — refund ' + esc(fmtAmt(back)) + '?', 'Take down')
        : '<button class="mini" data-user="' + esc(g.user) + '"' + dis + '>Take down all</button>';
    }
    rows.push('<tr class="u"><td colspan="4"><div class="uh"><span><span class="who">@' + esc(g.user) + '</span><span class="n">' +
      esc(plural(g.bets.length, 'bet')) + ' · ' + esc(coins(g.exposure)) + ' on the table</span></span>' + uact + '</div></td></tr>');
    g.bets.forEach(function(b){
      var bkey = 'bet:' + b.id, act;
      var pays = b.pays != null ? +b.pays : (+b.amount || 0) * ((+b.odds || 0) + 1);
      if(b.removable === false) act = '<span class="lock">stays</span>';
      else if(confirming(bkey)) act = confirmHTML(bkey, 'Refund ' + esc(fmtAmt(b.amount)) + ' to @' + esc(g.user) + '?', 'Take down');
      else act = '<button class="mini" data-bet="' + esc(b.id) + '"' + dis + '>Take down</button>';
      rows.push('<tr><td class="bl" title="' + esc((b.bet ? b.bet + ' · ' : '') + b.id) + '">' + esc(b.label || b.bet || b.type) +
        '<span class="o">' + esc(+b.odds || 0) + ' to 1</span></td>' +
        '<td class="num">' + esc(fmtAmt(b.amount)) + '</td><td class="num">' + esc(fmtAmt(pays)) + '</td><td class="act">' + act + '</td></tr>');
    });
  });
  rows.push('</tbody>');
  host.innerHTML = rows.join('');
  var total = t.total_on_table != null ? +t.total_on_table : bets.reduce(function(a, b){ return a + (+b.amount || 0); }, 0);
  $('t-total').textContent = coins(total) + ' on the table · ' + plural(groups.length, 'player');
  $('t-clear-wrap').innerHTML = confirming('clear')
    ? confirmHTML('clear', 'Refund all ' + esc(coins(total)) + ' to ' + esc(plural(groups.length, 'player')) + ' and clear the table?', 'Refund all')
    : '<button class="sec danger" id="t-clear"' + dis + '>Refund all &amp; clear table</button>';
}
function findBet(id){ return ((TABLE && TABLE.bets) || []).filter(function(b){ return b && b.id === id; })[0] || null; }
async function takeDown(key){
  var i = key.indexOf(':'), kind = i < 0 ? key : key.slice(0, i), arg = i < 0 ? '' : key.slice(i + 1), body, what;
  if(kind === 'bet'){
    var b = findBet(arg);
    body = { bet_id: arg }; what = b ? (b.label || b.bet || b.type) + (b.user ? ' (@' + b.user + ')' : '') : arg;
  } else if(kind === 'user'){
    body = { user: arg, all: true }; what = '@' + arg + '\'s bets';
  } else if(kind === 'clear'){
    return clearTable();
  } else return;
  CONFIRM = null; clearTimeout(_confirmT);
  renderTableCard(true);                     // drop the confirm now: a second click can't send it twice
  var d = await tableApi('POST', '/games/api/roulette/remove', body, "Can't take down");
  renderTableCard(true);
  if(!d) return;
  var paid = (d.credits || []).reduce(function(a, c){ return a + (+c.amount || 0); }, 0);
  var n = (d.removed || []).length;
  toast('Took down ' + (n > 1 ? plural(n, 'bet') : what) + (paid ? ' — refunded ' + coins(paid) : ''), 2600);
  if(isObj(d.table)) applyTable(d.table);
}
async function clearTable(){
  CONFIRM = null; clearTimeout(_confirmT);
  renderTableCard(true);
  var d = await tableApi('POST', '/games/api/roulette/clear', undefined, "Can't clear");
  renderTableCard(true);
  if(!d) return;
  var credits = d.credits || [];
  var paid = credits.reduce(function(a, c){ return a + (+c.amount || 0); }, 0);
  var users = dict(); credits.forEach(function(c){ if(c) users[c.user] = 1; });
  toast(paid ? 'Refunded ' + coins(paid) + ' to ' + plural(Object.keys(users).length, 'player') + ' — table cleared' : 'Table cleared', 3000);
  if(isObj(d.table)) applyTable(d.table);
}
function applyTable(t){
  if(!isObj(t)) return;
  TABLE = t; TABLE_AT = performance.now();
  tableSupport(true);
  renderFacts();
  renderTableCard(false);
  renderLimits();
  if(LASTV) renderBetOut(LASTV);
  renderLedgerSoon();
  renderDisplayNow();
}
async function validateBet(){
  clearTimeout(_vT);
  var bet = $('b-bet').value.trim();
  var seq = ++_vSeq;
  if(!bet){ renderBetOut(null); return null; }
  try{
    var r = await fetch('/games/api/roulette/validate?bet=' + encodeURIComponent(bet));
    var d = null;
    try{ d = await r.json(); }catch(_){ d = null; }
    if(seq !== _vSeq) return null;
    if(!d || (!r.ok && d.valid === undefined)){
      var msg = (d && typeof d.error === 'string' && d.error) || (d && typeof d.detail === 'string' && d.detail) || ('HTTP ' + r.status);
      d = { ok: false, error: 'validate failed — ' + msg };
    }
    renderBetOut(d);
    return d;
  }catch(e){
    if(seq === _vSeq) renderBetOut({ ok: false, error: 'validate failed — is the Games module running?' });
    return null;
  }
}
var PENDING = null, _pendT = null;   // a tester spin whose outcome we reveal only once it lands
async function spinWithBet(){
  var bet = $('b-bet').value.trim();
  if(!bet){ toast('Type a bet first'); $('b-bet').focus(); return; }
  var one = { bet: bet };
  var user = $('b-user').value.trim().slice(0, 40);
  var amt = parseFloat($('b-amount').value);
  if(user) one.user = user;
  if(isFinite(amt)) one.amount = amt;
  var body = { bets: [one] };
  if(user) body.user = user;
  var d = await api('POST', '/games/api/roulette/spin', body, "Can't spin");
  if(!d) return;
  PENDING = d;
  $('b-result').innerHTML = '<div class="note" style="margin-top:10px">Spinning… the outcome shows here when the ball lands.</div>';
  toast('Spinning — lands in ' + Math.round((+d.duration_ms || num(cfgFull().spin_seconds, 9) * 1000) / 1000) + 's');
  clearTimeout(_pendT);
  _pendT = setTimeout(function(){ if(PENDING === d) revealPending(); }, (+d.duration_ms || 9000) + 3000);
}
function checkPending(st){
  if(!PENDING) return;
  var id = PENDING.id;
  var landed = (st.spin && st.spin.id === id && st.state !== 'spinning') || (st.last && st.last.id === id);
  if(landed) revealPending();
}
function revealPending(){
  var sp = PENDING; PENDING = null; clearTimeout(_pendT);
  if(!sp || !sp.result) return;
  var res = sp.result;
  var rows = (sp.bets || []).map(function(b){
    var cls = !b.valid ? 'void' : (b.win ? 'win' : 'lose');
    var tag = !b.valid ? 'REFUND' : (b.win ? 'WIN' : 'LOSE');
    var money = '';
    if(b.amount != null && +b.amount){
      money = !b.valid ? 'returns ' + fmtAmt(b.returned)
            : (b.win ? '+' + fmtAmt(b.payout) + ' (returns ' + fmtAmt(b.returned) + ')' : fmtAmt(b.payout));
    }
    return '<div class="bres ' + cls + '"><b>' + tag + '</b>' + esc(b.label || b.bet) +
      (b.user ? ' <span class="u">@' + esc(b.user) + '</span>' : '') +
      (money ? ' · <span class="m">' + esc(money) + '</span>' : '') +
      (b.error ? ' <span class="e">— ' + esc(b.error) + '</span>' : '') + '</div>';
  }).join('');
  $('b-result').innerHTML =
    '<div class="landed"><span class="disc ' + esc(res.color || colorOf(res.number)) + '" style="width:36px;height:36px;font-size:15px">' + esc(res.number) + '</span>' +
    '<span class="note">Landed on <b style="color:var(--ink)">' + esc(res.number) + '</b> · ' + esc(detailsOf(res)) + '</span></div>' + rows;
}

/* ---------- Hex display (DISPLAY_SPEC §1/§5 — "Hex does the math") ---------- */
// The parse / request / error helpers are the page's (GamesPage.display, shared with the craps tab's card).
var ANNOUNCE_MAX = 50;
function dBody(p){
  p = p || parseDisplayLines($('d-lines').value);
  var body = {};
  var t = $('d-title').value.trim(); if(t) body.title = t;
  body.lines = p.lines.slice(0, ANNOUNCE_MAX);
  var e = $('d-empty').value.trim(); if(e) body.empty_text = e;
  var s = parseFloat($('d-seconds').value); if(isFinite(s)) body.seconds = s;
  var id = $('d-spin').value.trim(); if(id) body.spin_id = id;
  return body;
}
var BOARD_MAX = 100;
function boardBody(p){
  var body = {};
  var t = $('d-btitle').value.trim(); if(t) body.title = t;
  body.bets = p.lines.slice(0, BOARD_MAX);
  return body;
}
function renderDCount(){
  var p = parseDisplayLines($('d-lines').value), b = parseDisplayLines($('d-board').value, { signed: false });
  $('d-count').textContent = (p.lines.length || p.skipped || p.bad) ? countText(p, ANNOUNCE_MAX) : '';
  $('d-bcount').textContent = (b.lines.length || b.skipped || b.bad) ? countText(b, BOARD_MAX, 'bet') : '';
}
function boardOf(t){ return isObj(t) && isObj(t.display_board) ? t.display_board : null; }
function boardSummary(b){
  var n = Array.isArray(b.bets) ? b.bets.length : 0;
  return '“' + (b.title || 'ON THE TABLE') + '” · ' + (n ? plural(n, 'line') : 'empty') +
    (b.total != null && +b.total ? ' · ' + coins(b.total) : '');
}
function renderDisplayNow(){
  var a = STATE ? annLive(STATE.announce, STATE_AT) : null, b = boardOf(TABLE);
  var held = !!(a && STATE.state === 'spinning' && STATE.spin && (!a.spin_id || a.spin_id === STATE.spin.id));
  var hidden = !!(a && !held && !STATE.visible);      // /hide or /stop: still up, but not on stream
  var bHidden = !!(b && STATE && !STATE.visible && STATE.state !== 'spinning');
  var up = [];
  if(a) up.push(held ? 'waiting' : hidden ? 'up · hidden' : 'on screen');
  if(b) up.push('board up');
  var pill = $('d-pill');
  pill.textContent = up.length ? up.join(' · ') : 'nothing up';
  pill.className = 'pill' + (up.length ? ((a ? hidden : bHidden) ? ' off' : ' on') : '');
  var el = $('d-now');
  if(!STATE){ el.textContent = ''; return; }
  var html = !a ? 'Nothing up — after a spin with bets the wheel shows its own winners list.'
    : (hidden ? 'Up, but the wheel is hidden: ' : 'On screen: ') + '<b>' + esc(annSummary(a, annLeft(a, STATE_AT))) + '</b>' +
      (held ? ' · waits for the ball to land' : hidden ? ' · Show puts it back on stream' : '');
  var n = TABLE && Array.isArray(TABLE.bets) ? TABLE.bets.length : 0;
  if(b) html += '<br>Board: <b>Hex\'s ' + esc(boardSummary(b)) + '</b>' + (bHidden ? ' · the wheel is hidden — Show puts it on stream' : '');
  else if(TBL && TABLE) html += '<br>Board: this table\'s own bets (' + esc(n ? plural(n, 'bet') : 'none') + ')';
  el.innerHTML = html;
}
// The board routes reply with the TABLE (display_board in it); display only, like /announce.
async function dBoard(btn, path, body, okText){
  btn.disabled = true;
  var res = await displayReq(path, body);
  btn.disabled = false;
  if(res.ok){
    if(isObj(res.d.table)) applyTable(res.d.table);
    displayOut($('d-out'), true, okText(res.d), path, body, res);
  } else {
    displayOut($('d-out'), false, displayError(res), path, body, res);
    toast("Can't update the board", 2400);
  }
  renderDisplayNow();
}
function dSetBoard(){
  var p = parseDisplayLines($('d-board').value, { signed: false }), no = refuseText(p, 'board');
  if(no){ displayRefuse($('d-out'), no); toast('Nothing sent', 2400); return; }
  return dBoard($('d-setboard'), '/games/api/roulette/board', boardBody(p), function(d){
    var b = boardOf(d.table);
    toast('Board set');
    return 'Board set — ' + (b ? boardSummary(b) : 'showing Hex\'s bets');
  });
}
function dClearBoard(){
  return dBoard($('d-clearboard'), '/games/api/roulette/board/clear', undefined, function(){
    toast('Board cleared');
    return 'Board cleared — back to this table\'s own bets';
  });
}
async function dAnnounce(){
  var path = '/games/api/roulette/announce', btn = $('d-announce');
  var p = parseDisplayLines($('d-lines').value), no = refuseText(p, 'card');
  if(no){ displayRefuse($('d-out'), no); toast('Nothing sent', 2400); return; }
  var body = dBody(p);
  btn.disabled = true;
  var res = await displayReq(path, body);
  btn.disabled = false;
  if(res.ok){
    var a = res.d.announce;
    displayOut($('d-out'), true, 'Card up — ' + (annSummary(a, annLeft(a, performance.now())) || 'announced'), path, body, res);
    toast('Hex card up');
  } else {
    var msg = displayError(res);
    displayOut($('d-out'), false, msg, path, body, res);
    toast(res.status === 409 && res.d.error === 'stale' ? 'Stale spin id' : "Can't announce", 2400);
  }
}
async function dClear(){
  var path = '/games/api/roulette/announce/clear', btn = $('d-clear');
  btn.disabled = true;
  var res = await displayReq(path);
  btn.disabled = false;
  displayOut($('d-out'), res.ok, res.ok ? 'Card cleared — the wheel is back to its own winners list' : displayError(res), path, undefined, res);
  toast(res.ok ? 'Hex card cleared' : "Can't clear", res.ok ? 1600 : 2400);
}

/* ---------- ledger (GET /games/api/roulette/ledger + the socket's {"type":"ledger","game":"roulette"}) ---------- */
// The ledger is shared with craps (one seq space): this card only ever holds roulette events.
var LEDGER = [], LSEEN = dict(), LAST_SEQ = 0, LFRESH = dict(), LTRUNC = false, LSTATUS = 'loading';
var _lFetching = false, _lAgain = null, _ledgerBooted = false, LINK = null;
function bootLedger(){
  if(_ledgerBooted) return;
  _ledgerBooted = true;
  fetchLedger(0);
}
function addEvents(evts, live){
  var added = 0, unsorted = false;
  (evts || []).forEach(function(e){
    if(!isObj(e) || (e.game != null && e.game !== 'roulette')) return;
    var s = +e.seq;
    if(!isFinite(s) || LSEEN[s]) return;
    LSEEN[s] = 1;
    if(LEDGER.length && s < LEDGER[LEDGER.length - 1].seq) unsorted = true;
    LEDGER.push(e);
    if(live) LFRESH[s] = performance.now();
    added++;
  });
  if(!added) return 0;
  if(unsorted) LEDGER.sort(function(a, b){ return a.seq - b.seq; });
  if(LEDGER.length > LEDGER_MAX){
    LEDGER.splice(0, LEDGER.length - LEDGER_MAX).forEach(function(e){ delete LSEEN[e.seq]; });
    LTRUNC = true;
  }
  LAST_SEQ = LEDGER[LEDGER.length - 1].seq;
  renderLedgerSoon();
  return added;
}
async function fetchLedger(since){
  if(_lFetching){ _lAgain = Math.min(_lAgain == null ? since : _lAgain, since); return; }
  _lFetching = true;
  try{
    var s = since, got = 0;
    for(var page = 0; page < 3; page++){
      var res = await rawReq('GET', '/games/api/roulette/ledger?since=' + encodeURIComponent(s) + '&limit=' + LEDGER_PAGE);
      if(res.net || !res.ok || !Array.isArray(res.d.events)){
        if(res.status === 404 || res.status === 405 || res.d.error === 'unknown game'){ LSTATUS = 'unavailable'; tableSupport(false); }
        else if(!LEDGER.length) LSTATUS = 'error';
        break;
      }
      LSTATUS = 'live';
      // last_seq is the shared ledger's, so it is never below a seq it sent us — unless the
      // ledger started over (config wiped): then reload it whole.
      if(page === 0 && s > 0 && res.d.last_seq != null && isFinite(+res.d.last_seq) && +res.d.last_seq < s){
        LEDGER = []; LSEEN = dict(); LAST_SEQ = 0; LFRESH = dict(); LTRUNC = false;
        s = 0; got = 0; page = -1;
        continue;
      }
      var ev = res.d.events;
      // Older events are gone from the server's memory when a read from 0 comes back truncated
      // though the page limit didn't cut it (the first event's seq says nothing: craps shares the seqs).
      if(s === 0 && page === 0 && res.d.truncated && (ev.length < LEDGER_PAGE || +res.d.oldest_seq > 1)) LTRUNC = true;
      addEvents(ev, false);
      got += ev.length;
      if(ev.length < LEDGER_PAGE || got >= LEDGER_MAX) break;
      s = +ev[ev.length - 1].seq;
    }
  } finally {
    _lFetching = false;
    renderLedgerSoon();
    if(_lAgain != null){ var a = _lAgain; _lAgain = null; fetchLedger(a); }
  }
}
function onLedger(events){
  if(!Array.isArray(events) || !events.length) return;
  var first = Infinity;
  events.forEach(function(e){ if(e && isFinite(+e.seq)) first = Math.min(first, +e.seq); });
  // craps events share the seq space, so a hole may just be craps — re-reading costs one small GET
  var gap = LAST_SEQ > 0 && first > LAST_SEQ + 1;
  var from = LAST_SEQ;
  // the first read failed (or never ran) but the server is clearly pushing: read the history too
  var backfill = LSTATUS !== 'live' && LSTATUS !== 'loading';
  addEvents(events, true);
  if(TBL === false) return;   // a server without the roulette ledger route: never ask it
  if(backfill){ _ledgerBooted = true; fetchLedger(0); }
  else if(gap && _ledgerBooted) fetchLedger(from);
}
var _lRaf = false;
function renderLedgerSoon(){
  if(_lRaf) return;
  _lRaf = true;
  requestAnimationFrame(function(){ _lRaf = false; renderLedger(); });
}
function fmtTime(ts){
  var d = new Date((+ts || 0) * 1000);
  if(!isFinite(d.getTime())) return '';
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') + ':' + String(d.getSeconds()).padStart(2, '0');
}
function tile(label, value, sub, cls){
  return '<div class="tile"><span class="lbl">' + esc(label) + '</span><b class="' + (cls || '') + '">' + esc(value) +
    '</b><small>' + esc(sub) + '</small></div>';
}
function renderLedger(){
  var filt = cleanUser($('l-user').value).toLowerCase();
  var users = dict(), tIn = 0, tOut = 0;
  LEDGER.forEach(function(e){
    var u = String(e.user || '');
    var a = users[u] || (users[u] = { user: u, in: 0, out: 0, n: 0 });
    var amt = +e.amount || 0;
    if(e.type === 'debit'){ a.in += amt; tIn += amt; } else if(e.type === 'credit'){ a.out += amt; tOut += amt; }
    a.n++;
  });
  var exp = (TABLE && isObj(TABLE.exposure)) ? TABLE.exposure : {};
  function expOf(u){ return own(exp, u) ? +exp[u] || 0 : 0; }
  Object.keys(exp).forEach(function(u){ if(!users[u]) users[u] = { user: u, in: 0, out: 0, n: 0 }; });
  var onTable = TABLE && TABLE.total_on_table != null ? +TABLE.total_on_table
    : Object.keys(exp).reduce(function(a, u){ return a + expOf(u); }, 0);
  var house = tIn - tOut - onTable;

  // tiles
  $('l-sum').innerHTML =
    tile('Coins in', fmtAmt(tIn), cur() + ' taken · debits') +
    tile('Coins out', fmtAmt(tOut), cur() + ' paid · credits') +
    tile('On the table', fmtAmt(onTable), cur() + ' riding on the next spin') +
    tile('House net', signedAmt(house), house > 0 ? 'the house is up' : house < 0 ? 'the players are up' : 'even', house > 0 ? 'pos' : house < 0 ? 'neg' : '');

  // per-user nets
  var list = Object.keys(users).map(function(k){ var a = users[k]; a.exp = expOf(k); a.net = a.out + a.exp - a.in; return a; })
    .filter(function(a){ return a.user && (!filt || a.user.toLowerCase().indexOf(filt) >= 0); })
    .sort(function(a, b){ return (b.in + b.exp) - (a.in + a.exp) || a.user.localeCompare(b.user); });
  $('l-nets').innerHTML = '<thead><tr><th>Player</th><th class="num">Bet</th><th class="num">Paid</th><th class="num">On table</th><th class="num" title="paid + on table − bet">Player net</th></tr></thead><tbody>' +
    (list.length ? list.slice(0, 200).map(function(a){
      return '<tr><td class="wrap">@' + esc(a.user) + '</td><td class="num">' + esc(fmtAmt(a.in)) + '</td><td class="num">' + esc(fmtAmt(a.out)) +
        '</td><td class="num">' + esc(fmtAmt(a.exp)) + '</td><td class="num ' + (a.net > 0 ? 'pos' : a.net < 0 ? 'neg' : '') + '">' + esc(signedAmt(a.net)) + '</td></tr>';
    }).join('') : '<tr><td colspan="5" class="dim">' + (filt ? 'No player matches “' + esc(filt) + '”.' : 'No players yet.') + '</td></tr>') + '</tbody>';

  // events, newest first
  var rows = [], shown = 0, matched = 0, now = performance.now();
  for(var i = LEDGER.length - 1; i >= 0; i--){
    var e = LEDGER[i];
    if(filt && String(e.user || '').toLowerCase().indexOf(filt) < 0) continue;
    matched++;
    if(shown >= LEDGER_ROWS) continue;
    shown++;
    var cls = LFRESH[e.seq] && now - LFRESH[e.seq] < 1800 ? ' class="fresh"' : '';
    var deb = e.type === 'debit';
    rows.push('<tr' + cls + '><td class="num dim">' + esc(e.seq) + '</td><td class="mono dim opt" title="' + esc(new Date((+e.ts || 0) * 1000).toLocaleString()) + '">' + esc(fmtTime(e.ts)) +
      '</td><td>@' + esc(e.user) + '</td><td><span class="dc ' + (deb ? 'debit' : 'credit') + '">' + (deb ? 'DEBIT' : 'CREDIT') + '</span></td>' +
      '<td class="num ' + (deb ? 'neg' : 'pos') + '">' + (deb ? '−' : '+') + esc(fmtAmt(e.amount)) + '</td>' +
      '<td>' + esc(own(REASONS, e.reason) ? REASONS[e.reason] : (e.reason || '')) + '</td><td title="' + esc(e.bet_id || '') + '">' + esc(e.bet || '') + '</td>' +
      '<td class="mono dim opt">' + esc(e.roll_id || '') + '</td></tr>');
  }
  $('l-table').innerHTML = '<thead><tr><th class="num">Seq</th><th class="opt">Time</th><th>User</th><th></th><th class="num">Coins</th><th>Reason</th><th>Bet</th><th class="opt">Spin</th></tr></thead><tbody>' +
    (rows.length ? rows.join('') : '<tr><td colspan="8" class="dim">' +
      (LSTATUS === 'loading' ? 'Loading the ledger…' : LSTATUS === 'unavailable' ? 'The roulette ledger isn\'t available on this Hexcast yet.' :
        LSTATUS === 'error' ? 'Couldn\'t read the ledger — Reload to try again.' : filt ? 'No events for “' + esc(filt) + '”.' : 'No coin movements yet.') +
      '</td></tr>') + '</tbody>';
  Object.keys(LFRESH).forEach(function(s){ if(now - LFRESH[s] > 2000) delete LFRESH[s]; });

  // users datalist
  var dl = $('l-users'), names = Object.keys(users).filter(Boolean).sort();
  var dk = names.join('\n');
  if(dl._k !== dk){ dl._k = dk; dl.innerHTML = names.slice(0, 500).map(function(n){ return '<option value="' + esc(n) + '">'; }).join(''); }

  // pill + note
  var pill = $('l-pill');
  pill.textContent = LSTATUS === 'live' ? (LEDGER.length ? 'seq ' + LAST_SEQ : 'empty') : LSTATUS === 'loading' ? 'loading…' : 'unavailable';
  pill.className = 'pill' + (LSTATUS === 'live' ? ' on' : LSTATUS === 'loading' ? '' : ' off');
  var note = '';
  if(LEDGER.length){
    note = plural(LEDGER.length, 'event') + ' loaded (#' + LEDGER[0].seq + '–#' + LAST_SEQ + ')';
    if(filt) note += ' · ' + matched + ' for “' + filt + '”';
    if(matched > LEDGER_ROWS) note += ' · showing the newest ' + LEDGER_ROWS;
    if(LTRUNC) note += ' · older events aren\'t in memory, so the nets cover this window (the full ledger is config/games_ledger.jsonl)';
  }
  $('l-note').textContent = note;
}

/* ---------- settings (spec §9.4) ---------- */
var SET = [
  { k:'spin_seconds', t:'num', min:4, max:30 },
  { k:'result_seconds', t:'num', min:1, max:120 },
  { k:'cooldown_seconds', t:'num', min:0, max:3600 },
  { k:'bet_window_seconds', t:'num', min:5, max:300 },
  { k:'currency', t:'text', max:24 },
  { k:'min_bet', t:'int', min:1, max:1e9 },
  { k:'max_bet', t:'int', min:0, max:1e12 },
  { k:'hide_when_idle', t:'check' },
  { k:'show_when_bets', t:'check' },
  { k:'auto_spin', t:'check' },
  { k:'show_table', t:'check' },
  { k:'show_rules', t:'check' },
  { k:'table_max', t:'int', min:1, max:20 },
  { k:'table_position', t:'select' },
  { k:'show_user', t:'check' },
  { k:'show_bets', t:'check' },
  { k:'bets_max', t:'int', min:1, max:20 },
  { k:'spin_clip', t:'text' },
  { k:'land_clip', t:'text' },
  { k:'sfx', t:'check' },
  { k:'sfx_volume', t:'num', min:0, max:1 }
];
var DIRTY = {};
function paintVol(){ $('s-sfx_volume-val').textContent = Math.round(num($('s-sfx_volume').value, 0.5) * 100) + '%'; }
function paintDirty(){ $('s-dirty').textContent = Object.keys(DIRTY).length ? 'unsaved changes' : ''; }
function fillOne(f, c){
  var el = $('s-' + f.k); if(!el) return;
  if(f.t === 'check') el.checked = !!c[f.k];
  else el.value = (c[f.k] == null ? '' : c[f.k]);
  if(f.t === 'select' && el.value !== String(c[f.k])) el.value = DEFAULTS[f.k];
}
// force=false: never clobber a field the user has touched (dirty) or is focused in.
function fillSettings(force){
  var c = cfgFull();
  SET.forEach(function(f){
    var el = $('s-' + f.k); if(!el) return;
    if(!force && (DIRTY[f.k] || el === document.activeElement)) return;
    fillOne(f, c);
  });
  paintVol(); paintDirty();
  $('g-timer-secs').placeholder = num(c.bet_window_seconds, 20) + ' s';
}
function collectSettings(){
  var out = {};
  SET.forEach(function(f){
    var el = $('s-' + f.k); if(!el) return;
    if(f.t === 'check'){ out[f.k] = el.checked; return; }
    if(f.t === 'num' || f.t === 'int'){
      var v = parseFloat(el.value);
      if(!isFinite(v)) return;                 // blank / junk: leave the saved value alone
      if(f.t === 'int') v = Math.round(v);
      if(f.min != null) v = Math.max(f.min, v);
      if(f.max != null) v = Math.min(f.max, v);
      out[f.k] = v; return;
    }
    if(f.t === 'select'){ if(el.value) out[f.k] = String(el.value); return; }
    var s = String(el.value || '').trim();
    if(f.max) s = s.slice(0, f.max);
    if(f.k === 'currency' && !s) return;       // 1..24 chars: blank keeps the saved name
    out[f.k] = s;
  });
  return out;
}
async function saveSettings(){
  var out = collectSettings();
  var min = out.min_bet != null ? out.min_bet : cfgFull().min_bet;
  if(out.max_bet != null && out.max_bet !== 0 && out.max_bet < min){
    toast('Max bet must be 0 (no max) or at least the min bet (' + fmtAmt(min) + ')', 3000);
    $('s-max_bet').focus();
    return;
  }
  var d = await api('POST', '/games/api/config', { roulette: out }, "Can't save");
  if(!d) return;
  DIRTY = {};
  var conf = d.config || (d.roulette ? { roulette: d.roulette } : null);
  if(conf && conf.roulette) onConfig(conf);
  else CFG_RAW = Object.assign({}, CFG_RAW || {}, out);
  fillSettings(true);
  toast('Saved');
}

/* ---------- API card ---------- */
function buildApiCard(){
  var rows = [
    ['GET|POST /games/api/roulette/spin', 'start a spin (alias <code>/play</code>). <code>user</code>, <code>duration</code> (4–30 s), <code>wait</code> (reply at landing), <code>test</code> (no history, no clips, the table untouched), <code>bet</code>+<code>amount</code> for one bet or JSON <code>bets:[{user,bet,amount}]</code> (≤200) — direct bets: resolved in the reply, not on the table, not in the ledger, <code>overrides</code> or <code>x</code>/<code>y</code>/<code>scale</code>. Returns the spin incl. result, resolved bets, the table\'s <code>settlements</code> and <code>lands_at</code>, plus <code>table</code> (after landing with <code>wait</code>, else the pre-spin table) and, with <code>wait</code>, this spin\'s <code>ledger</code> events'],
    ['GET|POST /games/api/roulette/bet', 'a bet on the table for the next spin: <code>{user, bet, amount}</code> or <code>{bets:[…]}</code> (≤200; whole coins, min / max bet per line; the same user + bet adds to that line) → <code>accepted</code>, <code>rejected</code> (with <code>error</code>), <code>debits:[{user,amount,bet_id,seq,reason}]</code>, <code>table</code>. 400 if every bet was rejected. The first bet starts the countdown when auto-spin is on'],
    ['GET|POST /games/api/roulette/remove', '<code>{bet_id}</code>, <code>{user, bet}</code> or <code>{user, all:true}</code> → <code>removed</code> (each with its <code>refund</code>), <code>credits</code>, <code>ledger</code>, <code>table</code>. 400 when nothing matches'],
    ['GET|POST /games/api/roulette/clear', 'refund every bet on the table (credits, reason <code>refund</code>) and stop the countdown'],
    ['GET /games/api/roulette/table', 'the table: <code>bets</code>, <code>exposure</code>, <code>total_on_table</code>, <code>bets_open</code>, <code>auto_spin_in_ms</code>, <code>last_seq</code>, <code>currency</code>, <code>min_bet</code>, <code>max_bet</code>, <code>display_board</code>'],
    ['GET /games/api/roulette/user/{name}', 'that user\'s bets, exposure and roulette session debits / credits / net'],
    ['GET /games/api/roulette/ledger?since=0&limit=500', 'the roulette coin movements with <code>seq</code> &gt; since, oldest first (limit ≤ 5000): <code>events</code>, <code>last_seq</code>, <code>truncated</code>. The seqs are shared with craps, so gaps are normal'],
    ['GET /games/api/ledger?since=0&game=', 'every game\'s events in one stream (each carries <code>game</code>); <code>game=roulette</code> or <code>craps</code> for one'],
    ['GET|POST /games/api/roulette/timer', 'start (or restart) the countdown now — <code>seconds</code> (5–300, default the bet window), with or without bets, auto-spin on or off. At 0 the wheel spins by itself. 409 busy unless idle → <code>auto_in_ms</code>, <code>table</code>, <code>state</code>. Same for <code>/games/api/craps/timer</code>'],
    ['GET|POST /games/api/roulette/timer/cancel', 'stop the countdown (auto or started with /timer) → <code>cancelled</code>, <code>table</code>, <code>state</code>'],
    ['GET /games/api/roulette/last', 'last committed spin: <code>{"result":…,"spin":…}</code>'],
    ['GET /games/api/roulette/history?limit=20', 'landed spins, newest first, plus stats (hot/cold, streak, counts)'],
    ['POST /games/api/roulette/history/clear', 'clear history + stats (GET works too)'],
    ['GET /games/api/roulette/validate?bet=…', 'parse a bet string: <code>valid</code>, type, label, numbers, odds or <code>error</code>'],
    ['GET /games/api/roulette/bets', 'bet-type reference: type, syntax examples, odds'],
    ['GET|POST /games/api/roulette/announce', 'Hex does the math: Hex\'s own winners card for the spin on screen (or the last one), in place of the computed winners list. <code>title</code>, <code>lines:[{user,amount,text}]</code> (≤50; one line as <code>?user=&amp;amount=&amp;text=</code>), <code>empty_text</code>, <code>seconds</code> (1–120, default: the rest of the result), <code>currency</code>, <code>spin_id</code> (not that spin → 409 <code>{"error":"stale"}</code>). Posted mid-spin it waits for the landing; a new spin clears it. Returns <code>announce</code> + <code>state</code>'],
    ['GET|POST /games/api/roulette/announce/clear', 'take Hex\'s card down now'],
    ['GET|POST /games/api/roulette/board', 'Hex\'s own "on the table" board, shown before the spin in place of this table\'s bets: <code>{title, bets:[{user,text,amount}]}</code> (≤100, amount ≥ 0) → <code>table.display_board</code> <code>{title, bets, total}</code> (memory only). <code>{"bets":[]}</code> = an empty board. Display only: never bets or ledger'],
    ['GET|POST /games/api/roulette/board/clear', 'back to the board computed from this table\'s bets (same as <code>{"clear":true}</code>)'],
    ['GET|POST /games/api/roulette/show · hide', 'show the idle wheel / hide it (hide is 409 mid-spin)'],
    ['GET|POST /games/api/stop', 'abort animations and hide everything (a started spin still counts); stops the countdowns'],
    ['GET /games/api/status', 'overlay count + live state per game (roulette state carries the <code>table</code>)'],
    ['GET|POST /games/api/config', 'read / merge-save <code>{"roulette":{…}}</code>'],
    ['GET /games/api', 'this reference as JSON'],
    ['WS /games/ws/overlay · /games/ws/panel', 'live <code>config</code> / <code>state</code> / <code>stop</code> messages; panel sockets also get <code>{"type":"ledger","game":"roulette","events":[…]}</code> (one message per game) — a bank bot can listen here instead of polling']
  ];
  $('api-table').innerHTML = rows.map(function(r){ return '<tr><td>' + esc(r[0]) + '</td><td>' + r[1] + '</td></tr>'; }).join('');
  var bets = [
    ['straight', '<code>17</code> · <code>0</code> · <code>00</code>', 35],
    ['split', '<code>split:17-20</code> · <code>17/20</code> · zero splits <code>0/1</code> <code>0/2</code> <code>00/2</code> <code>00/3</code> <code>0/00</code>', 17],
    ['street', '<code>street:13</code> · <code>street:13-14-15</code> · <code>13/14/15</code>', 11],
    ['trio', '<code>0/1/2</code> · <code>0/00/2</code> · <code>00/2/3</code> (also <code>trio:</code>)', 11],
    ['corner', '<code>corner:17</code> · <code>corner:17-18-20-21</code> · <code>17/18/20/21</code>', 8],
    ['basket', '<code>basket</code> · <code>topline</code> · <code>five</code> · <code>0/00/1/2/3</code>', 6],
    ['six_line', '<code>line:13</code> · <code>line:13-18</code> · <code>sixline:13</code>', 5],
    ['dozen', '<code>dozen1</code> · <code>d2</code> · <code>3rd12</code> · <code>13-24</code>', 2],
    ['column', '<code>col1</code> · <code>column2</code> · <code>c3</code>', 2],
    ['red / black', '<code>red</code> · <code>r</code> · <code>black</code> · <code>b</code>', 1],
    ['odd / even', '<code>odd</code> · <code>even</code>', 1],
    ['low / high', '<code>low</code> · <code>1-18</code> · <code>manque</code> · <code>high</code> · <code>19-36</code> · <code>passe</code>', 1]
  ];
  $('bet-table').innerHTML = bets.map(function(b){
    return '<tr><td>' + esc(b[0]) + '</td><td>' + b[1] + '</td><td style="white-space:nowrap">' + b[2] + ' to 1</td></tr>';
  }).join('');
  var base = location.protocol + '//' + location.host;
  var J = function(o){ return '"' + JSON.stringify(o).replace(/"/g, '\\"') + '"'; };
  $('api-curl').textContent = [
    'curl -X POST ' + base + '/games/api/roulette/spin',
    'curl "' + base + '/games/api/roulette/spin?user=bob&bet=red&amount=100"',
    'curl -X POST ' + base + '/games/api/roulette/spin -H "Content-Type: application/json" -d "{\\"user\\":\\"bob\\",\\"wait\\":true,\\"bets\\":[{\\"user\\":\\"bob\\",\\"bet\\":\\"split:17/20\\",\\"amount\\":50}]}"',
    'curl -X POST ' + base + '/games/api/roulette/bet -H "Content-Type: application/json" -d ' + J({ user: 'alice', bet: 'red', amount: 100 }),
    'curl "' + base + '/games/api/roulette/bet?user=bob&bet=17&amount=10"',
    'curl -X POST ' + base + '/games/api/roulette/timer -H "Content-Type: application/json" -d ' + J({ seconds: 30 }),
    'curl -X POST ' + base + '/games/api/roulette/timer/cancel',
    'curl "' + base + '/games/api/roulette/ledger?since=0&limit=500"',
    'curl ' + base + '/games/api/roulette/table',
    'curl -X POST ' + base + '/games/api/roulette/remove -H "Content-Type: application/json" -d ' + J({ bet_id: 'rb-1a2b3c4d' }),
    'curl -X POST ' + base + '/games/api/roulette/board -H "Content-Type: application/json" -d ' + J({ bets: [{ user: 'alice', text: 'Red', amount: 100 }] }),
    'curl "' + base + '/games/api/roulette/validate?bet=corner:17"',
    'curl ' + base + '/games/api/roulette/last',
    'curl "' + base + '/games/api/roulette/history?limit=5"',
    'curl -X POST ' + base + '/games/api/roulette/announce -H "Content-Type: application/json" -d "{\\"title\\":\\"WINNERS\\",\\"lines\\":[{\\"user\\":\\"bob\\",\\"amount\\":100,\\"text\\":\\"Red\\"}]}"',
    'curl -X POST ' + base + '/games/api/roulette/show',
    'curl -X POST ' + base + '/games/api/stop'
  ].join('\n');
  $('api-bank').innerHTML = [
    '<b>Hex is the bank.</b> Hexcast never holds a balance — Hex\'s <b>hexbank</b> does (<code>hexbank.debit</code> / <code>credit</code> are placeholder names for Hex\'s own functions). Pick one route per bet and never pay the same bet from both: <b>bets on the spin call</b> (<code>/spin</code> with <code>bets</code> — Hex pays <code>returned</code> itself, nothing in the ledger) or <b>the table</b> below (the durable one: every coin in the ledger).',
    '<b>Place:</b> on <code>!bet red 100</code>: check <code>hexbank.balance(user) ≥ 100</code> → <code>POST /games/api/roulette/bet</code> <code>{"user","bet","amount"}</code> → for every <code>debits[]</code> entry <code>{user, amount, bet_id, seq}</code>: <code>hexbank.debit(user, amount)</code> and remember its <code>seq</code> as done. Rejected bets moved nothing: reply with <code>rejected[].error</code>. 409 <code>bets_closed</code> = the ball is rolling: try again after it lands.',
    '<b>Spin:</b> <code>POST /games/api/roulette/timer</code> (or turn on <b>auto-spin</b>: the first bet starts the countdown) — at 0 the wheel spins by itself with every bet on the table. <code>!spin</code> → <code>/spin</code> works too.',
    '<b>Tail the ledger:</b> <code>GET /games/api/roulette/ledger?since=&lt;last_seq&gt;</code> every second or two (or on each <code>{"type":"ledger","game":"roulette"}</code> push on <code>WS /games/ws/panel</code>). For each event, oldest first: seq already done → skip; <code>credit</code> (<code>win</code>, <code>remove</code>, <code>refund</code>) → <code>hexbank.credit(user, amount)</code>; <code>debit</code> → <code>hexbank.debit(user, amount)</code> (a bet Hex didn\'t place itself — this panel\'s <b>Place bet</b>, a curl); then save its <code>seq</code> as <code>last_seq</code>. Running craps too? Tail <code>/games/api/ledger</code> once for both games — one seq space.',
    '<b>Hex does the math instead (timer only):</b> Hex keeps its own bets → <code>POST /timer</code> + <code>POST /board</code> (its bets on screen) → the wheel spins at 0 → read the RESULT (<code>/last</code> or the state) → Hex settles from its hexbank → <code>POST /announce</code> its winners card. Nothing touches the ledger.',
    '<b>Restarts:</b> the table and the ledger are saved on disk; bets stay on the table and <code>seq</code> carries on. A spin in the air during a restart is lost — nothing was settled, the bets ride on the next spin.'
  ].map(function(s){ return '<li>' + s + '</li>'; }).join('');
}

/* ---------- Edit Mode + placement editor (the soundboard's openEditor(), §9) ---------- */
function editMode(){ return document.body.classList.contains('edit-mode'); }   // the page's Edit Mode button toggles it
var ED = null;   // open editor: { refresh() }
function themeColor(theme, which){
  var r = R(), t = r && r.THEMES && r.THEMES[theme];
  if(t){
    var cands = [t[which], t[which + '_color'], t[which + 'Color'], t.pockets && t.pockets[which], t.pocket && t.pocket[which]];
    for(var i = 0; i < cands.length; i++){
      if(typeof cands[i] === 'string' && /^#[0-9a-f]{6}$/i.test(cands[i])) return cands[i];
    }
  }
  return CLASSIC[which];
}
// A sample table for the editor (preview only): the board + countdown show while you place the wheel.
function redNums(){ return Object.keys(RED_NUMS).sort(function(a, b){ return a - b; }); }
function sampleTable(cfg){
  var dozen2 = []; for(var i = 13; i <= 24; i++) dozen2.push(String(i));
  var odds = []; for(i = 1; i <= 36; i += 2) odds.push(String(i));
  var bets = [
    { id:'rb-demo0001', user:'alice', bet:'red', label:'Red', type:'red', numbers:redNums(), odds:1, amount:100 },
    { id:'rb-demo0002', user:'bob', bet:'17', label:'Straight 17', type:'straight', numbers:['17'], odds:35, amount:10 },
    { id:'rb-demo0003', user:'carol', bet:'dozen2', label:'2nd 12', type:'dozen', numbers:dozen2, odds:2, amount:50 },
    { id:'rb-demo0004', user:'dave', bet:'split:17/20', label:'Split 17/20', type:'split', numbers:['17','20'], odds:17, amount:20 },
    { id:'rb-demo0005', user:'alice', bet:'odd', label:'Odd', type:'odd', numbers:odds, odds:1, amount:40 }
  ];
  var exposure = {}, total = 0;
  bets.forEach(function(b){
    b.pays = b.amount * (b.odds + 1); b.removable = true; b.placed_at = 0;
    exposure[b.user] = (exposure[b.user] || 0) + b.amount; total += b.amount;
  });
  return { bets: bets, exposure: exposure, total_on_table: total, bets_open: true,
    auto_spin_in_ms: Math.round(clamp(num(cfg.bet_window_seconds, 20), 5, 300) * 1000), last_seq: 0,
    currency: cfg.currency, min_bet: cfg.min_bet, max_bet: cfg.max_bet, display_board: null, demo: true };
}
// Demo settlement of the sample table (preview only — the server settles real bets).
function demoSettle(table, res){
  var st = table.bets.map(function(b){
    var win = b.numbers.indexOf(String(res.number)) >= 0;
    return { bet_id: b.id, user: b.user, bet: b.bet, label: b.label, type: b.type, amount: b.amount, odds: b.odds,
      win: win, payout: win ? b.amount * b.odds : -b.amount, credit: win ? b.amount * (b.odds + 1) : 0 };
  });
  var credits = dict(), paid = 0;
  st.forEach(function(s){ if(s.credit){ credits[s.user] = (credits[s.user] || 0) + s.credit; paid += s.credit; } });
  return { settlements: st,
    credits: Object.keys(credits).map(function(u){ return { user: u, amount: credits[u] }; }),
    table_summary: { bets: st.length, wagered: table.total_on_table, paid: paid, net: table.total_on_table - paid } };
}
function colorRow(k, label){
  return '<div class="editor-row">' +
    '<label>' + label + '</label>' +
    '<input type="color" id="ed-' + k + '" title="' + label + ' pockets">' +
    '<button class="pick" data-reset="' + k + '" title="Use the theme\'s own colour">theme default</button>' +
    '<div class="value" id="ed-' + k + '-val" style="flex:1;text-align:left;">theme</div>' +
  '</div>';
}
function openEditor(){
  if(ED) return;
  var base = cfgFull();
  var w = pick(base, EDIT_KEYS);
  w.x = clamp(num(w.x, 50), 0, 100);
  w.y = clamp(num(w.y, 50), 0, 100);
  w.scale = clamp(num(w.scale, 1.25), 0.2, 5);
  w.history_count = clamp(Math.round(num(w.history_count, 10)), 1, 20);
  w.table_max = clamp(Math.round(num(w.table_max, 6)), 1, 20);

  var ov = document.createElement('div');
  ov.className = 'modal-overlay';
  ov.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <span class="title">Roulette — placement &amp; look</span>
        <button class="close" title="Close">×</button>
      </div>
      <div class="canvas-preview" id="ed-preview">
        <div class="grid-overlay"></div>
        <div class="gstage"><div class="gwheel" id="ed-wheel"></div></div>
        <div class="pmsg" id="ed-msg" style="display:none"></div>
      </div>
      <div class="editor-controls">
        <div class="editor-row">
          <label>Scale</label>
          <input type="range" id="scaleslider" min="0.2" max="5" step="0.05" value="${w.scale}">
          <div class="value" id="scaleval">${w.scale.toFixed(2)}x</div>
        </div>
        <div class="editor-row">
          <label>Position</label>
          <div class="value" style="flex:1;text-align:left;">
            x:<span id="xval">${w.x.toFixed(0)}</span>% y:<span id="yval">${w.y.toFixed(0)}</span>%
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
        <div class="editor-row">
          <label>Theme</label>
          <select id="ed-theme">${THEMES.map(function(t){ return '<option value="' + t[0] + '">' + t[1] + '</option>'; }).join('')}</select>
        </div>
        ${colorRow('red_color', 'Red')}
        ${colorRow('black_color', 'Black')}
        ${colorRow('green_color', 'Green')}
        <div class="editor-row wrap">
          <label>Result</label>
          <select id="ed-result_position">
            <option value="center">Centre — over the turret</option>
            <option value="below">Below the wheel</option>
            <option value="above">Above the wheel</option>
          </select>
          <label class="tog"><input type="checkbox" id="ed-show_result"> show result</label>
          <label class="tog"><input type="checkbox" id="ed-result_details"> details line</label>
        </div>
        <div class="editor-row wrap">
          <label>History</label>
          <label class="tog"><input type="checkbox" id="ed-show_history"> show strip</label>
          <input type="number" id="ed-history_count" min="1" max="20" step="1" style="flex:0 0 80px">
          <span class="value" style="min-width:0;text-align:left">results</span>
        </div>
        <div class="editor-row wrap">
          <label>Board</label>
          <label class="tog"><input type="checkbox" id="ed-show_table"> on the table</label>
          <select id="ed-table_position">${TABLE_POSITIONS.map(function(t){ return '<option value="' + t[0] + '">' + t[1] + '</option>'; }).join('')}</select>
          <input type="number" id="ed-table_max" min="1" max="20" step="1" style="flex:0 0 80px">
          <span class="value" style="min-width:0;text-align:left">lines</span>
        </div>
        <div class="editor-row">
          <label>Rules</label>
          <label class="tog"><input type="checkbox" id="ed-show_rules"> how-to-play box (while bets are open)</label>
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

  var q = function(sel){ return ov.querySelector(sel); };
  var preview = q('#ed-preview');
  var view = makeView(preview, q('.gstage'), q('#ed-wheel'));
  var slider = q('#scaleslider');
  function full(){ return Object.assign({}, cfgFull(), w); }
  var table0 = sampleTable(full());
  function hasTable(){ return !!view.inst && typeof view.inst.setTable === 'function'; }

  // Real renderer, sound off.
  var r = R();
  if(r && typeof r.create === 'function'){
    try{ view.inst = r.create(view.wheel, full(), { sound: false }); }
    catch(e){ console.error('[games] renderer create failed:', e); view.inst = null; }
  }
  if(!view.inst){
    view.wheel.classList.add('empty');
    viewMsg(q('#ed-msg'), 'Wheel renderer not loaded — placement still works.');
  } else if(hasTable()){
    // A sample table (board + countdown) to place around; ▶ Preview shows the result and winners.
    safe(function(){ view.inst.setTable(table0); });
    if(STATE) safe(function(){ view.inst.setHistory(STATE.history || []); });
  } else if(STATE){
    // Show the history strip + last result so the result/details/history options are visible right away.
    var hist = STATE.history || [];
    safe(function(){ view.inst.setHistory(hist); });
    if(STATE.last && STATE.last.result){
      var shown = Object.assign({}, STATE.last, { overrides: {}, elapsed_ms: +STATE.last.duration_ms || 0 });
      safe(function(){ view.inst.showResult(shown); });
    }
  }
  if(hasTable()){
    var tnote = document.createElement('div');
    tnote.className = 'note'; tnote.style.marginTop = '-4px';
    tnote.textContent = 'Sample bets on a sample table — ▶ Preview spins a local demo ball; nothing is bet, spun on stream or saved.';
    preview.parentNode.insertBefore(tnote, preview.nextSibling);
  }

  function update(){
    placeView(view, w.x, w.y, w.scale);
    q('#xval').textContent = w.x.toFixed(0);
    q('#yval').textContent = w.y.toFixed(0);
    q('#scaleval').textContent = w.scale.toFixed(2) + 'x';
  }
  update();

  // Appearance -> renderer, coalesced to one setConfig per frame.
  var _cfgPending = false;
  function pushCfg(){
    if(_cfgPending || !view.inst) return;
    _cfgPending = true;
    requestAnimationFrame(function(){
      _cfgPending = false;
      if(!view.inst) return;
      safe(function(){ view.inst.setConfig(full()); });
      requestResize(view);
    });
  }

  // Drag-to-position (pointer capture), exactly like the soundboard.
  var previewActive = false;
  var dragging = false, startX, startY, startMx, startMy;
  view.wheel.addEventListener('pointerdown', function(e){
    if(previewActive) return;   // don't drag while previewing
    dragging = true;
    startX = w.x; startY = w.y;
    startMx = e.clientX; startMy = e.clientY;
    try{ view.wheel.setPointerCapture(e.pointerId); }catch(_){}
    preview.classList.add('dragging');
    e.preventDefault();
  });
  view.wheel.addEventListener('pointermove', function(e){
    if(!dragging) return;
    var rect = preview.getBoundingClientRect();
    var dx = ((e.clientX - startMx) / rect.width) * 100;
    var dy = ((e.clientY - startMy) / rect.height) * 100;
    w.x = clamp(startX + dx, 0, 100);
    w.y = clamp(startY + dy, 0, 100);
    update();
  });
  function endDrag(e){
    if(!dragging) return;
    dragging = false;
    preview.classList.remove('dragging');
    try{ view.wheel.releasePointerCapture(e.pointerId); }catch(_){}
  }
  view.wheel.addEventListener('pointerup', endDrag);
  view.wheel.addEventListener('pointercancel', endDrag);

  // Scale slider
  slider.oninput = function(){ w.scale = clamp(num(slider.value, 1.25), 0.2, 5); update(); };

  // Quick positions
  ov.querySelectorAll('.quick-positions button').forEach(function(b){
    b.onclick = function(){
      var p = b.dataset.pos.split(',').map(Number);
      w.x = p[0]; w.y = p[1]; update();
    };
  });

  // Appearance rows
  var themeSel = q('#ed-theme'), posSel = q('#ed-result_position'), tposSel = q('#ed-table_position');
  var cbs = ['show_result', 'result_details', 'show_history', 'show_table', 'show_rules'];
  var histNum = q('#ed-history_count'), tmaxNum = q('#ed-table_max');
  function paintColors(){
    ['red', 'black', 'green'].forEach(function(c){
      var k = c + '_color', inp = q('#ed-' + k), v = w[k];
      inp.value = (typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v)) ? v : themeColor(w.theme, c);
      inp.classList.toggle('dflt', !v);
      q('#ed-' + k + '-val').textContent = v ? v : 'theme';
    });
  }
  function syncControls(){
    slider.value = w.scale;
    themeSel.value = w.theme;
    if(themeSel.value !== w.theme){ themeSel.value = 'classic'; w.theme = 'classic'; }
    posSel.value = w.result_position;
    if(posSel.value !== w.result_position){ posSel.value = 'center'; w.result_position = 'center'; }
    tposSel.value = w.table_position;
    if(tposSel.value !== w.table_position){ tposSel.value = 'right'; w.table_position = 'right'; }
    cbs.forEach(function(k){ q('#ed-' + k).checked = !!w[k]; });
    histNum.value = w.history_count;
    tmaxNum.value = w.table_max;
    paintColors();
  }
  syncControls();
  themeSel.onchange = function(){ w.theme = themeSel.value; paintColors(); pushCfg(); };
  posSel.onchange = function(){ w.result_position = posSel.value; pushCfg(); };
  tposSel.onchange = function(){ w.table_position = tposSel.value; pushCfg(); };
  cbs.forEach(function(k){ q('#ed-' + k).onchange = function(){ w[k] = this.checked; pushCfg(); }; });
  histNum.oninput = function(){
    var v = parseInt(histNum.value, 10);
    if(!isFinite(v)) return;
    w.history_count = clamp(v, 1, 20); pushCfg();
  };
  histNum.onchange = function(){ histNum.value = w.history_count; };
  tmaxNum.oninput = function(){
    var v = parseInt(tmaxNum.value, 10);
    if(!isFinite(v)) return;
    w.table_max = clamp(v, 1, 20); pushCfg();
  };
  tmaxNum.onchange = function(){ tmaxNum.value = w.table_max; };
  ['red', 'black', 'green'].forEach(function(c){
    var k = c + '_color', inp = q('#ed-' + k);
    inp.addEventListener('input', function(){ w[k] = inp.value; paintColors(); pushCfg(); });
    q('[data-reset="' + k + '"]').onclick = function(){ w[k] = ''; paintColors(); pushCfg(); };
  });

  // ---- ▶ Preview: a local demo spin in the modal (random pocket + seed, configured duration; no server call) ----
  var previewBtn = q('.modal-actions .preview');
  var previewTok = 0, previewTimer = null;
  function previewDone(tok){
    if(tok !== previewTok) return;
    clearTimeout(previewTimer);
    previewActive = false;
    previewBtn.classList.remove('playing');
    previewBtn.textContent = '▶ Preview';
  }
  function stopPreview(){
    if(!previewActive) return;
    previewTok++;
    clearTimeout(previewTimer);
    previewActive = false;
    previewBtn.classList.remove('playing');
    previewBtn.textContent = '▶ Preview';
    if(view.inst) safe(function(){ view.inst.reset(); });
    if(hasTable()) safe(function(){ view.inst.setTable(table0); });
  }
  if(!view.inst){ previewBtn.disabled = true; previewBtn.title = 'wheel renderer not loaded'; }
  previewBtn.onclick = function(){
    if(!view.inst) return;
    if(previewActive){ stopPreview(); return; }
    var rr = R(), cfg = full();
    var list = (rr && rr.AMERICAN && rr.AMERICAN.length) ? rr.AMERICAN : AMERICAN;
    var label = list[Math.floor(Math.random() * list.length)];
    var result = safe(function(){ return rr.resultFor(label, 'american'); });
    if(!result){ toast('Preview failed — see the browser console', 2400); return; }
    var dur = Math.round(clamp(num(cfg.spin_seconds, 9), 4, 30) * 1000);
    var spin = {
      id: 'preview-' + Date.now().toString(36), game: 'roulette', result: result, user: '', test: true,
      seed: Math.floor(Math.random() * 2147483647), wheel: 'american',
      duration_ms: dur, result_ms: Math.round(num(cfg.result_seconds, 6) * 1000), elapsed_ms: 0,
      bets: [], summary: null, overrides: {}
    };
    if(hasTable()){
      // the sample table rides on the demo spin (a real test spin never touches the table)
      Object.assign(spin, demoSettle(table0, result));
      safe(function(){ view.inst.setTable(table0); });
    }
    previewActive = true;
    previewBtn.classList.add('playing');
    previewBtn.textContent = '⏸ Stop';
    var tok = ++previewTok;
    var p = null;
    try{ p = view.inst.play(spin); }
    catch(e){ console.error('[games] preview failed:', e); previewDone(tok); return; }
    if(p && typeof p.then === 'function') p.then(function(){ previewDone(tok); }, function(){ previewDone(tok); });
    previewTimer = setTimeout(function(){ previewDone(tok); }, dur + 250);   // renderer without a promise
  };

  // ---- actions ----
  function snapshot(){
    var o = pick(w, EDIT_KEYS);
    o.x = Math.round(w.x * 100) / 100;
    o.y = Math.round(w.y * 100) / 100;
    o.scale = Math.round(w.scale * 100) / 100;
    return o;
  }
  function onKey(e){ if(e.key === 'Escape'){ e.preventDefault(); close(); } }
  function onWinResize(){ fitView(view); }
  if(!view.ro) window.addEventListener('resize', onWinResize);
  function close(){
    stopPreview();
    document.removeEventListener('keydown', onKey);
    window.removeEventListener('resize', onWinResize);
    if(view.ro) view.ro.disconnect();
    if(view.inst){ var inst = view.inst; view.inst = null; safe(function(){ inst.destroy(); }); }
    ov.remove();
    ED = null;
  }
  document.addEventListener('keydown', onKey);
  // The buttons are looked up inside the modal's chrome: the renderer's DOM in the preview
  // comes first in the modal and may use the same class names.
  q('.modal-header .close').onclick = close;
  q('.modal-actions .cancel').onclick = close;
  closeOnBackdrop(ov, close);

  q('.modal-actions .test').onclick = async function(){
    var d = await api('POST', '/games/api/roulette/spin', { test: true, overrides: snapshot() }, "Can't test");
    if(d) toast('fired to OBS — not saved yet');
  };
  q('.modal-actions .save').onclick = async function(){
    var d = await api('POST', '/games/api/config', { roulette: snapshot() }, "Can't save");
    if(!d) return;
    var conf = d.config || (d.roulette ? { roulette: d.roulette } : null);
    if(conf && conf.roulette) onConfig(conf);
    else { CFG_RAW = Object.assign({}, CFG_RAW || {}, snapshot()); setPocketVars(cfgFull()); mirrorConfig(); }
    toast('saved roulette placement');
    // Modal stays open so you can keep fine-tuning. Close via × / Cancel / Esc / click-outside.
  };
  q('.modal-actions .reset').onclick = function(){
    Object.assign(w, pick(DEFAULTS, EDIT_KEYS));
    syncControls(); update(); pushCfg();
    toast('defaults restored — not saved yet');
  };

  ED = { refresh: pushCfg, close: close };   // saved config changed elsewhere (e.g. Settings): re-render with our working look
}

function onState(st){
  if(!st || typeof st !== 'object') return;
  STATE = st; STATE_AT = performance.now();
  if(isObj(st.table)){ TABLE = st.table; TABLE_AT = STATE_AT; tableSupport(true); }
  mirrorState(st);
  renderReadout();
  renderFacts();
  renderTableCard(false);
  renderLimits();
  if(LASTV) renderBetOut(LASTV);
  renderLast(st);
  renderHistory(st.history || []);
  maybeFetchStats(st);
  checkPending(st);
  renderLedgerSoon();
  renderDisplayNow();
}
function setLink(on){
  var p = $('ws-pill');
  p.textContent = on ? 'live' : 'offline — reconnecting';
  p.className = 'pill ' + (on ? 'on' : 'off');
  if(on && LINK === false && _ledgerBooted && TBL !== false) fetchLedger(LAST_SEQ);   // catch up on what we missed offline
  LINK = !!on;
}

/* ---------- styles + markup ---------- */
// The rules only this tab's markup uses (cards, mirror, tables, bars, the editor modal ... are the page's).
var CSS = `
/* ---- Roulette tab: the rules only its markup uses (the cards, live mirror, tables, bars, editor modal ...
       are the page's, shared with the other games' tabs) ---- */
:root{
  /* pocket colours for chips (classic theme; follow the saved *_color overrides) */
  --pk-red:#c0282d; --pk-black:#16161a; --pk-green:#0f7a3c;
}
  #g-readout{font:800 30px/1 var(--mono);color:var(--ink);font-variant-numeric:tabular-nums}
  #g-state{font:600 12px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--dim)}
  #g-state.spinning{color:var(--gold)}
  #g-state.result{color:var(--good)}
  #g-state.cooldown{color:var(--warn)}
  #g-sub{font-size:12px;color:var(--dim);margin-top:4px;min-height:18px}
  .last:not(:where(.gstage *)){display:flex;align-items:center;gap:12px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;
        background:#0c0c11;min-height:66px}
  .last:not(:where(.gstage *)) .none{color:var(--dim);font-size:13px}
  .last:not(:where(.gstage *)) .meta{display:flex;flex-direction:column;gap:2px;min-width:0}
  .last:not(:where(.gstage *)) .det{font:700 12px var(--mono);letter-spacing:.08em;color:var(--ink)}
  .last:not(:where(.gstage *)) .who{font-size:12px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .disc{flex:0 0 auto;width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;
        font:800 18px/1 var(--mono);color:#fff;box-shadow:inset 0 0 0 2px rgba(255,255,255,.2),0 2px 8px rgba(0,0,0,.5)}
  .disc.red{background:var(--pk-red)} .disc.black{background:var(--pk-black)} .disc.green{background:var(--pk-green)}
  body.edit-mode #edit-place{background:#3a2a18;border-color:var(--amber);color:var(--amber-soft)}
  body.edit-mode #card-roulette{border-color:var(--amber);cursor:pointer;position:relative}
  body.edit-mode #card-roulette::after{content:"✎";position:absolute;top:12px;right:16px;color:var(--amber-soft);
        text-shadow:0 1px 2px #000;font-size:16px}
  body.edit-mode #card-roulette:hover{border-color:var(--amber-soft)}
  .hist{display:flex;flex-wrap:wrap;gap:6px;min-height:30px}
  .hist:empty::after{content:'No spins yet.';color:var(--dim);font-size:13px}
  .pk{display:inline-flex;align-items:center;justify-content:center;min-width:30px;height:30px;padding:0 7px;
      border-radius:99px;font:700 13px/1 var(--mono);color:#fff;border:1px solid rgba(255,255,255,.14);
      font-variant-numeric:tabular-nums}
  .pk.sm{min-width:26px;height:26px;padding:0 6px;font-size:12px}
  .pk.red{background:var(--pk-red)} .pk.black{background:var(--pk-black)} .pk.green{background:var(--pk-green)}
  .pk.first{box-shadow:0 0 0 2px var(--gold)}
  .srow{margin-top:12px}
  .srow .top{display:flex;justify-content:space-between;gap:10px;font-size:11px;margin-bottom:4px}
  .srow .lbl{color:var(--dim);text-transform:uppercase;letter-spacing:.08em;font-weight:600}
  .srow .cnt{font:12px var(--mono);color:var(--ink);text-align:right}
  .bar:not(:where(.gstage *)) i{display:block;height:100%}
  .bar:not(:where(.gstage *)) i.red{background:var(--pk-red)} .bar:not(:where(.gstage *)) i.black{background:#3a3a46}
  .bar:not(:where(.gstage *)) i.green{background:var(--pk-green)}
  .bar:not(:where(.gstage *)) i.a{background:#4ea1ff} .bar:not(:where(.gstage *)) i.b{background:#a970ff}
  .chips{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
  .chips .none{color:var(--dim);font-size:12px}
  .hc{display:grid;grid-template-columns:auto 1fr;gap:8px 12px;align-items:center;margin-top:14px}
  .hc .lbl{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;font-weight:600}
  .bt{grid-template-columns:minmax(0,2fr) minmax(0,1fr) minmax(0,1fr)}
  .bout .chips{margin-top:8px}
  #b-result .landed{display:flex;align-items:center;gap:10px;margin-top:10px}
  .limits{font-size:12px;color:var(--dim);margin-top:8px}
  .bres .seq{font:12px var(--mono);color:var(--dim)}
  .tag{display:inline-block;font:700 10px/1 var(--mono);letter-spacing:.08em;padding:3px 6px;border-radius:99px;
       border:1px solid var(--line);color:var(--dim);white-space:nowrap;vertical-align:1px}
  .tag.real{color:var(--red-soft);border-color:var(--red-deep);background:#2a1414}
  .banner{display:none;margin-bottom:16px;padding:12px 16px;border-radius:12px;border:1px solid #4d3a10;
          background:#211a0b;color:var(--warn);font-size:14px}
  .banner.on{display:block}
  .banner b{color:#ffd27a}
  #g-state.betting{color:#4ea1ff}
  .facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:12px}
  .facts > div{background:#0c0c11;border:1px solid var(--line);border-radius:8px;padding:6px 9px;min-width:0}
  .facts .lbl{display:block;font-size:10px;color:var(--dim);letter-spacing:.1em;text-transform:uppercase;font-weight:600}
  .facts b{display:block;min-height:20px;font:700 13px/1.5 var(--mono);color:var(--ink);
           white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .facts b.warn{color:var(--warn)} .facts b.good{color:var(--good)} .facts b.dim{color:var(--dim)}
  #card-roulette .row input{flex:0 1 96px;min-width:0;width:auto}
  table.tb tr.u td{background:#0f0f16;border-top:1px solid var(--line-hi, #35354a)}
  table.tb tr.u .uh{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
  table.tb tr.u .who{font-weight:700;color:var(--ink);overflow-wrap:anywhere}
  table.tb tr.u .n{color:var(--dim);font-size:12px;margin-left:6px}
  table.tb td.bl{color:var(--ink);overflow-wrap:break-word}
  table.tb td.bl .o{display:block;font:11.5px var(--mono);color:var(--dim);white-space:nowrap}
  .tempty{padding:18px 8px;color:var(--dim);font-size:13px;text-align:center}
  button.mini{padding:4px 9px;font:600 12px/1.3 inherit;border-radius:7px;background:#1c1c25;color:var(--ink);
              border:1px solid var(--line);cursor:pointer;white-space:nowrap}
  button.mini:hover{border-color:var(--accent)}
  button.mini.yes,#tab-roulette button.danger{background:#3a1414;border-color:var(--red-deep);color:var(--red-soft)}
  button.mini.yes:hover,#tab-roulette button.danger:hover{background:#4a1a1a;border-color:var(--red)}
  .confirm{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
  .confirm .q{font-size:12px;color:var(--warn);white-space:normal;text-align:right}
  .lock:not(:where(.gstage *)){color:var(--dim);font-size:12px}
  .lsum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:2px 0 12px}
  .tile{background:#0c0c11;border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:0}
  .tile .lbl{display:block;font-size:10px;color:var(--dim);letter-spacing:.1em;text-transform:uppercase;font-weight:600}
  .tile b{display:block;font:800 19px/1.3 var(--mono);color:var(--ink);font-variant-numeric:tabular-nums;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tile small{display:block;font-size:11px;color:var(--dim)}
  .pos:not(:where(.gstage *)){color:var(--good) !important} .neg:not(:where(.gstage *)){color:var(--red-soft) !important}
  .lbar{display:flex;gap:9px;flex-wrap:wrap;align-items:flex-end}
  .lbar label.f{flex:1 1 200px}
  .dc{font:800 10px/1 var(--mono);letter-spacing:.08em;padding:3px 6px;border-radius:99px}
  .dc.debit{color:var(--red-soft);border:1px solid var(--red-deep);background:#2a1414}
  .dc.credit{color:var(--good);border:1px solid #1d4d33;background:#0e2118}
  .lscroll{max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin-top:10px}
  .lscroll.short{max-height:220px}
  .lnote{font-size:12px;color:var(--dim);margin-top:8px}
  .dsp{grid-template-columns:minmax(0,1.2fr) minmax(0,1.4fr) minmax(0,.8fr) minmax(0,1fr)}
  .dgrid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:18px}
  .dgrid > div{min-width:0}
  .dgrid .dsp{grid-template-columns:repeat(2,minmax(0,1fr))}
  .dgrid .dl{margin-top:12px}
  /* ---- api ---- */
  ol.steps{margin:6px 0 4px;padding-left:22px;font-size:13px;color:var(--dim)}
  ol.steps li{margin:0 0 7px}
  ol.steps b{color:var(--ink)}

@media (max-width: 860px){
    .bt{grid-template-columns:1fr 1fr}
    .dsp{grid-template-columns:1fr 1fr}
    .dgrid{grid-template-columns:1fr}
    .lsum{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width: 560px){
    .bt{grid-template-columns:1fr}
    .dsp,.dgrid .dsp{grid-template-columns:1fr}
}
`;
var MARKUP = `
  <div class="card">
    <h2>OBS browser source</h2>
    <p class="hint">Add as a Browser source in OBS (1920×1080). It scales to whatever size you give it and stays transparent until a game runs. One source can show every game, or give each game its own source (so it sits on its own layer) with the game-only URL.</p>
    <div class="url"><a id="u-overlay" target="_blank" rel="noopener"></a><button class="sec" id="copy-overlay">Copy</button></div>
    <div class="url"><a id="u-overlay-game" target="_blank" rel="noopener"></a><button class="sec" id="copy-overlay-game">Copy</button></div>
  </div>

  <div class="banner" id="rt-banner"><b>The roulette table and spin timer aren't on this Hexcast yet.</b> The page is new but
    the server is still the old one — restart Hexcast to load the table, the spin timer and the roulette ledger. Spins,
    bets on a spin and Hex's own cards work as before.</div>

  <div class="card" id="card-roulette">
    <h2>Roulette <span class="pill" id="ws-pill">connecting…</span></h2>
    <p class="hint">Live mirror of what the overlay shows — it plays the very same spins. The server draws every result with a cryptographic RNG; nothing on this page (or the API) can pick the number. Turn on <b>Edit Mode</b> and click this card to place and style the wheel.</p>
    <div class="game">
      <div class="mirror" id="mirror">
        <div class="gstage" id="mirror-stage"><div class="gwheel off" id="mirror-wheel"></div></div>
        <span class="mtag" id="mirror-tag">hidden</span>
        <div class="pmsg" id="mirror-msg" style="display:none"></div>
      </div>
      <div class="gside">
        <div class="kv">State</div>
        <div class="readout"><span id="g-readout">—</span><span id="g-state">offline</span></div>
        <div id="g-sub"></div>
        <div class="facts">
          <div><span class="lbl">On the table</span><b id="g-f-total">—</b></div>
          <div><span class="lbl">Spin timer</span><b id="g-f-timer">—</b></div>
          <div><span class="lbl">Bets</span><b id="g-f-open">—</b></div>
        </div>
        <div class="kv" style="margin-top:14px">Last result</div>
        <div class="last" id="g-last"><span class="none">No spins yet.</span></div>
        <div class="row">
          <button class="act" id="g-spin">Spin</button>
          <button class="sec" id="g-show" title="Show the idle wheel on stream">Show</button>
          <button class="sec" id="g-hide" title="Hide the wheel (not possible mid-spin)">Hide</button>
          <button class="sec edit-only" id="edit-place">✎ Edit placement</button>
        </div>
        <div class="row" style="margin-top:9px">
          <input type="number" id="g-timer-secs" min="5" max="300" step="1" placeholder="20 s" title="Countdown seconds (5–300) — blank: the bet window from Settings">
          <button class="sec" id="g-timer" title="Start the countdown now, with or without bets: the wheel spins by itself at 0">Start timer</button>
          <button class="sec" id="g-timer-cancel" title="Stop the countdown (auto-spin or started here); nothing spins">Cancel timer</button>
        </div>
      </div>
    </div>
  </div>

  <div class="two">
    <div class="card" id="card-table">
      <h2>On the table <span class="pill" id="t-pill">no bets</span></h2>
      <p class="hint">Bets riding on the next spin — placed by chat, a bot (<code>/roulette/bet</code>) or the form next door. Every bet can come down until the ball is thrown; a take-down refunds the stake (a <code>credit</code> in the ledger). At the landing the winners are paid as ledger credits and every bet leaves the table. Frozen while the ball is rolling.</p>
      <div class="tscroll"><table class="tb" id="t-bets"></table></div>
      <div class="row"><span class="note" id="t-total"></span><span style="flex:1"></span><span id="t-clear-wrap"></span></div>
    </div>

    <div class="card" id="card-bets">
      <h2>Place a bet <span class="tag real">real coins</span></h2>
      <p class="hint"><b>Check</b> is a dry run: how the bet string is read (the same parser bots hit). <b>Place</b> is real: the bet goes on the table for the next spin and a <b>debit</b> goes in the ledger, so Hex's ledger tail takes those coins from that user — and pays a win as a ledger <b>credit</b>. Test with test names. <b>Spin with this bet</b> is the direct route: a spin with just this bet riding on it — not on the table, not in the ledger (an invalid bet comes back refunded).</p>
      <div class="grid bt">
        <label class="f">Bet <input id="b-bet" placeholder="red, 17, split:17/20, dozen2…" autocomplete="off" spellcheck="false"></label>
        <label class="f">Amount <input type="number" id="b-amount" min="0" step="any" placeholder="whole coins"></label>
        <label class="f">User <input id="b-user" maxlength="40" placeholder="viewer name" autocomplete="off" spellcheck="false"></label>
      </div>
      <div class="ex" id="b-examples"></div>
      <div class="limits" id="b-limits"></div>
      <div class="bout" id="b-out"><span class="none">Type a bet (or pick an example) to see how it resolves.</span></div>
      <div class="row">
        <button class="sec" id="b-check">Check</button>
        <button class="act" id="b-place" title="Put the bet on the table for the next spin — writes a real ledger debit">Place bet</button>
        <button class="sec" id="b-spin" title="Spin now with just this bet on the spin (no table, no ledger)">Spin with this bet</button>
      </div>
      <div id="b-placed"></div>
      <div id="b-result"></div>
    </div>
  </div>

  <div class="card" id="card-display">
    <h2>Hex display <span class="pill" id="d-pill">nothing up</span></h2>
    <p class="hint">For when <b>Hex does the math</b>: Hex keeps the bets and pays from its own hexbank; Hexcast still spins the wheel and shows what Hex says. <b>Set board</b> shows Hex's bets as the "on the table" board before the spin, in place of this table's own (pair it with <b>Start timer</b> for a countdown). <b>Announce</b> puts Hex's winners card up for the spin on screen (or the last one), in place of the wheel's own winners list; posted while the ball is still rolling, it waits for the landing, and a new spin clears it. One line each — <code>user amount text…</code>, e.g. <code>alice 200 Red</code>: the amount is optional (never negative on the board), <code>-</code> as the user means none. No lines at all = a card with just the empty text (nobody won) / an empty board. Blank fields use the server's defaults. Nothing here touches the table's bets, the ledger or the history; the mirror above shows it all.</p>
    <div class="dgrid">
      <div>
        <div class="kv">Winners card — announce</div>
        <div class="grid dsp">
          <label class="f">Title<input id="d-title" maxlength="40" placeholder="WINNERS" autocomplete="off" spellcheck="false"></label>
          <label class="f">Empty text<input id="d-empty" maxlength="60" placeholder="No winners" autocomplete="off"></label>
          <label class="f">Seconds (1–120)<input type="number" id="d-seconds" min="1" max="120" step="1" placeholder="auto"></label>
          <label class="f">Spin id<input id="d-spin" maxlength="40" placeholder="latest spin" autocomplete="off" spellcheck="false" title="Optional: only put the card up if this is the spin on screen (or the last one) — otherwise HTTP 409 stale"></label>
        </div>
        <label class="f dl">Lines — user amount text…<textarea id="d-lines" class="dlines" rows="4" spellcheck="false" placeholder="alice 200 Red&#10;@bob -50 Black&#10;carol 350 Straight up 17"></textarea></label>
        <div class="row">
          <button class="act" id="d-announce">Announce</button>
          <button class="sec" id="d-clear" title="Take Hex's card down now">Clear</button>
          <span class="note" id="d-count"></span>
        </div>
      </div>
      <div>
        <div class="kv">On the table — board</div>
        <div class="grid dsp">
          <label class="f">Board title<input id="d-btitle" maxlength="40" placeholder="ON THE TABLE" autocomplete="off" spellcheck="false"></label>
        </div>
        <label class="f dl">Bets — user amount text…<textarea id="d-board" class="dlines" rows="4" spellcheck="false" placeholder="alice 100 Red&#10;bob 25 Straight 17"></textarea></label>
        <div class="row">
          <button class="act" id="d-setboard">Set board</button>
          <button class="sec" id="d-clearboard" title="Back to the board computed from this table's own bets">Clear board</button>
          <span class="note" id="d-bcount"></span>
        </div>
      </div>
    </div>
    <div class="dnow" id="d-now"></div>
    <div class="bout" id="d-out"><span class="none">The request and the server's reply show here.</span></div>
  </div>

  <div class="card" id="card-ledger">
    <h2>Ledger <span class="pill" id="l-pill">loading…</span></h2>
    <p class="hint">Every roulette coin movement of the table route, in order: a <b>debit</b> means the bank takes coins from the user (a bet), a <b>credit</b> means it pays them (a win, a take-down, a refund). Each event has a strictly increasing <code>seq</code> shared with the craps table — gaps here are craps events. New events stream in live. Bets on a spin call (<b>Spin with this bet</b>) are never in the ledger.</p>
    <div class="lsum" id="l-sum"></div>
    <div class="lbar">
      <label class="f">Filter by user <input id="l-user" list="l-users" placeholder="everyone" autocomplete="off" spellcheck="false"></label>
      <button class="sec" id="l-all">Everyone</button>
      <button class="sec" id="l-reload" title="Re-read the ledger from the server">Reload</button>
    </div>
    <datalist id="l-users"></datalist>
    <div class="lscroll short"><table class="lg" id="l-nets"></table></div>
    <div class="lscroll"><table class="lg" id="l-table"></table></div>
    <div class="lnote" id="l-note"></div>
  </div>

  <div class="card" id="card-history">
    <h2>History &amp; stats</h2>
    <p class="hint">Last 20 landed results, newest first. Stats cover every spin since the last clear or restart — test spins never count.</p>
    <div class="hist" id="h-chips"></div>
    <div id="h-stats"></div>
    <div class="row"><span class="note" id="h-spins"></span><span style="flex:1"></span><button class="sec" id="h-clear">Clear history</button></div>
  </div>

  <div class="card" id="card-settings">
    <h2>Settings</h2>
    <p class="hint">Timing, the table's money rules, captions and sounds for the American (double-zero) wheel. Min / max bet and the currency apply to bets on the table (<code>/roulette/bet</code>); bets sent with a spin call are unchanged. Placement and look live in <b>Edit Mode</b> (click the Roulette card). Soundboard clips fire server-side on the soundboard overlay at launch / landing — keep that browser source in the scene too.</p>
    <div class="grid">
      <label class="f">Spin seconds (4–30)<input type="number" id="s-spin_seconds" min="4" max="30" step="0.5"></label>
      <label class="f">Result seconds (1–120)<input type="number" id="s-result_seconds" min="1" max="120" step="0.5"></label>
      <label class="f">Cooldown seconds (0–3600)<input type="number" id="s-cooldown_seconds" min="0" max="3600" step="1"></label>
      <label class="f">Bet window seconds (5–300)<input type="number" id="s-bet_window_seconds" min="5" max="300" step="1"></label>
      <label class="f">Currency name<input id="s-currency" maxlength="24" placeholder="hexcoins" autocomplete="off" spellcheck="false"></label>
      <label class="f">Min bet (whole coins)<input type="number" id="s-min_bet" min="1" max="1000000000" step="1"></label>
      <label class="f">Max bet (0 = no max)<input type="number" id="s-max_bet" min="0" max="1000000000000" step="1"></label>
      <label class="f">Launch clip<input id="s-spin_clip" list="clip-list" placeholder="— none —" autocomplete="off" spellcheck="false"></label>
      <label class="f">Landing clip<input id="s-land_clip" list="clip-list" placeholder="— none —" autocomplete="off" spellcheck="false"></label>
      <label class="f">Bets shown (1–20)<input type="number" id="s-bets_max" min="1" max="20" step="1"></label>
      <label class="f">Table lines (1–20)<input type="number" id="s-table_max" min="1" max="20" step="1"></label>
      <label class="f">Table board position<select id="s-table_position"></select></label>
      <label class="f">Built-in sound volume
        <span class="rng"><input type="range" id="s-sfx_volume" min="0" max="1" step="0.05"><b id="s-sfx_volume-val">50%</b></span>
      </label>
    </div>
    <div class="grid" style="margin-top:14px">
      <label class="f sw"><input type="checkbox" id="s-hide_when_idle"> Hide the wheel when idle</label>
      <label class="f sw"><input type="checkbox" id="s-show_when_bets"> Keep it on screen while bets are down</label>
      <label class="f sw"><input type="checkbox" id="s-auto_spin"> Auto-spin after the bet window</label>
      <label class="f sw"><input type="checkbox" id="s-show_table"> Show the "on the table" board</label>
      <label class="f sw"><input type="checkbox" id="s-show_rules"> Show the "how to play" box</label>
      <label class="f sw"><input type="checkbox" id="s-show_user"> Show "@user spins" caption</label>
      <label class="f sw"><input type="checkbox" id="s-show_bets"> Show winners list after landing</label>
      <label class="f sw"><input type="checkbox" id="s-sfx"> Built-in ball sounds</label>
    </div>
    <div class="row">
      <button class="act" id="s-save">Save settings</button>
      <button class="sec" id="s-revert">Revert</button>
      <span class="dirty" id="s-dirty"></span>
    </div>
  </div>

  <div class="card" id="card-api">
    <h2>API</h2>
    <p class="hint">Everything a bot needs is plain HTTP; every endpoint returns <code>{"ok": true|false, …}</code>. While a spin is in flight, on screen, or cooling down, new spins (and a new timer) get HTTP 409 <code>{"error":"busy","retry_in_ms":…}</code>; bets, take-downs and refunds while the ball is rolling get 409 <code>{"error":"bets_closed","retry_in_ms":…}</code>. Full reference as JSON at <code>GET /games/api</code>; the long version is in <code>docs/games.md</code>.</p>
    <div class="tscroll"><table class="api" id="api-table"></table></div>
    <h3>Bet syntax — American wheel (case-insensitive, spaces ignored; bare lists use <code>/</code> or <code>,</code>; even money loses on 0 and 00)</h3>
    <div class="tscroll"><table class="api" id="bet-table"></table></div>
    <h3>Examples</h3>
    <pre id="api-curl"></pre>
    <h3>The table route and the bank (hexcoins)</h3>
    <ol class="steps" id="api-bank"></ol>
  </div>
`;

/* ---------- Roulette is being uninstalled ---------- */
// The page removes the tab right after this: stop everything that would keep running against it.
function teardown(){
  TIMERS.forEach(function(t){ clearInterval(t); });
  TIMERS = [];
  [_vT, _pendT, _statsT, _confirmT].forEach(function(t){ clearTimeout(t); });
  if(ED && ED.close) ED.close();
  if(MIR){
    if(MIR.ro) MIR.ro.disconnect();
    if(MIR.inst){ var inst = MIR.inst; MIR.inst = null; safe(function(){ inst.destroy(); }); }
  }
  var st = document.getElementById('rt-style');
  if(st) st.remove();
  delete window.RoulettePanel;
}

/* ---------- wiring ---------- */
function init(){
  var st = document.createElement('style');
  st.id = 'rt-style';
  st.textContent = CSS;
  document.head.appendChild(st);
  HOST.innerHTML = MARKUP;

  var overlayUrl = location.protocol + '//' + location.host + '/games/overlay';
  $('u-overlay').textContent = overlayUrl;
  $('u-overlay').href = overlayUrl;
  $('copy-overlay').addEventListener('click', function(){ copyText(overlayUrl); });
  var gameUrl = overlayUrl + '?game=roulette';
  $('u-overlay-game').textContent = gameUrl;
  $('u-overlay-game').href = gameUrl;
  $('copy-overlay-game').addEventListener('click', function(){ copyText(gameUrl); });

  $('edit-place').addEventListener('click', function(e){ e.stopPropagation(); openEditor(); });
  $('card-roulette').addEventListener('click', function(e){
    if(!editMode()) return;
    if(e.target.closest('button, a, input, select, textarea, label, .pill')) return;
    openEditor();
  });

  $('g-spin').addEventListener('click', async function(){
    var d = await api('POST', '/games/api/roulette/spin', {}, "Can't spin");
    if(d) toast('Spinning — lands in ' + Math.round((+d.duration_ms || num(cfgFull().spin_seconds, 9) * 1000) / 1000) + 's');
  });
  $('g-show').addEventListener('click', async function(){ if(await api('POST', '/games/api/roulette/show', undefined, "Can't show")) toast('Wheel shown'); });
  $('g-hide').addEventListener('click', async function(){ if(await api('POST', '/games/api/roulette/hide', undefined, "Can't hide")) toast('Wheel hidden'); });
  $('g-timer').addEventListener('click', startTimer);
  $('g-timer-secs').addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); startTimer(); } });
  $('g-timer-cancel').addEventListener('click', cancelTimer);

  // on the table: take-down / confirm (event delegation survives re-renders)
  $('card-table').addEventListener('click', function(e){
    var b = e.target.closest('button');
    if(!b || b.disabled) return;
    if(b.dataset.bet) askConfirm('bet:' + b.dataset.bet);
    else if(b.dataset.user) askConfirm('user:' + b.dataset.user);
    else if(b.id === 't-clear') askConfirm('clear');
    else if(b.dataset.yes) takeDown(b.dataset.yes);
    else if(b.dataset.no) cancelConfirm();
  });
  $('h-clear').addEventListener('click', async function(){
    if(!confirm('Clear the roulette history and stats?')) return;
    if(await api('POST', '/games/api/roulette/history/clear')) toast('History cleared');
  });

  // bet tester
  $('b-bet').addEventListener('input', function(){ clearTimeout(_vT); _vT = setTimeout(validateBet, 350); });
  $('b-bet').addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); validateBet(); } });
  $('b-amount').addEventListener('input', function(){ if($('b-bet').value.trim()){ clearTimeout(_vT); _vT = setTimeout(validateBet, 250); } });
  $('b-user').addEventListener('input', function(){ if(LASTV) renderBetOut(LASTV); });   // "adds to …" is per user
  $('b-check').addEventListener('click', validateBet);
  $('b-place').addEventListener('click', placeBet);
  $('b-spin').addEventListener('click', spinWithBet);
  $('b-examples').addEventListener('click', function(e){
    var b = e.target.closest('button[data-bet]');
    if(!b) return;
    $('b-bet').value = b.dataset.bet;
    validateBet();
  });

  // Hex display
  $('d-lines').addEventListener('input', renderDCount);
  $('d-board').addEventListener('input', renderDCount);
  $('d-announce').addEventListener('click', dAnnounce);
  $('d-clear').addEventListener('click', dClear);
  $('d-setboard').addEventListener('click', dSetBoard);
  $('d-clearboard').addEventListener('click', dClearBoard);
  renderDCount();   // a reload can restore the boxes' text
  TIMERS.push(setInterval(function(){
    if(!HOST.classList.contains('sel')) return;
    if(STATE && STATE.announce) renderDisplayNow();   // the card's countdown
    renderTimer();                                    // the spin timer's
  }, 250));

  // ledger
  $('l-user').addEventListener('input', renderLedgerSoon);
  $('l-all').addEventListener('click', function(){ $('l-user').value = ''; renderLedgerSoon(); });
  $('l-reload').addEventListener('click', function(){
    LEDGER = []; LSEEN = dict(); LAST_SEQ = 0; LFRESH = dict(); LTRUNC = false;
    if(TBL === false){ LSTATUS = 'unavailable'; renderLedger(); return; }
    LSTATUS = 'loading'; _ledgerBooted = true;
    renderLedger(); fetchLedger(0);
  });

  // settings
  SET.forEach(function(f){
    var el = $('s-' + f.k); if(!el) return;
    var mark = function(){ DIRTY[f.k] = true; paintDirty(); };
    el.addEventListener('input', mark);
    el.addEventListener('change', mark);
    // A config update skipped this field while it was focused; if it was left untouched, catch it up now.
    el.addEventListener('blur', function(){ if(!DIRTY[f.k]){ fillOne(f, cfgFull()); if(f.k === 'sfx_volume') paintVol(); } });
  });
  $('s-sfx_volume').addEventListener('input', paintVol);
  $('s-save').addEventListener('click', saveSettings);
  $('s-revert').addEventListener('click', function(){ DIRTY = {}; fillSettings(true); });

  $('s-table_position').innerHTML = TABLE_POSITIONS.map(function(t){ return '<option value="' + t[0] + '">' + esc(t[1]) + '</option>'; }).join('');

  MIR = makeView($('mirror'), $('mirror-stage'), $('mirror-wheel'));
  if(!window.ResizeObserver) window.addEventListener('resize', function(){ fitView(MIR); });

  buildApiCard();
  renderExamples();
  fillSettings(true);
  renderReadout();
  renderFacts();
  renderTableCard(true);
  renderLimits();
  renderLedger();
}

init();

// register with the page, then catch up on what its socket already delivered
window.GamePanels = window.GamePanels || {};
window.GamePanels.roulette = {
  onConfig: onConfig, onState: onState,
  onStop: function(game){ if(!game || game === 'roulette') onStop(); },
  onLedger: onLedger, onLink: setLink,
  onTab: function(key){ if(key === 'roulette' && MIR && !window.ResizeObserver) fitView(MIR); },   // ResizeObserver refits the mirror on its own
  onRemove: teardown
};
var last = GP.last || {};
if(last.config) onConfig(last.config);
if(last.states && last.states.roulette) onState(last.states.roulette);
if(last.link != null) setLink(last.link);

// debugging / tests
window.RoulettePanel = { openEditor: openEditor,
  state: function(){ return { STATE: STATE, TABLE: TABLE, LEDGER: LEDGER, CFG: cfgFull() }; } };
})();
