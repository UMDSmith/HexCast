/*
 * Hexcast Games — shared panel helpers                        panel_common.js
 *
 * games_panel.html loads this before any game's panel script. It starts window.GamesPage, the page's
 * hand-over to the game panels, with what every game tab needs:
 *
 *   esc(s)                            HTML-escape
 *   toast(msg[, ms])                  the little pill at the bottom of the page
 *   copyText(str)                     clipboard + "Copied"
 *   api(method, path[, body, verb])   JSON request that toasts on failure (a 409 busy -> busyToast);
 *                                     returns the body, or null
 *   rawReq(method, path[, body])      the same but never toasts: {net, status, ok, d}
 *   busyToast(d, verb)                "Can't spin — the wheel is still spinning. Try again in 3s"
 *                                     (the wheel's wording: craps and the round games word their own)
 *   loadClipList()                    fills the page's <datalist id="clip-list"> with the soundboard's clips
 *   display                           the "Hex display" helpers (line parser, request, error text ...):
 *                                     the roulette and craps tabs' "Hex does the math" cards
 *
 * and, added by the page itself (games_panel.html) / round_common.js:
 *
 *   last                              the last config / state per game / socket link, for a panel that loads late
 *   registry                          the games of GET /games/api/registry, in tab order
 *   gameScript(key)                   a game's renderer script URL (from the registry)
 *   round                             the panel the round games share (round_common.js)
 */
(function () {

function isObj(o){ return !!o && typeof o === 'object' && !Array.isArray(o); }
function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
// (annSummary below shows the time left the way the roulette tab does)
function fmtSec(ms){
  ms = Math.max(0, ms);
  if(ms >= 60000){ var s = Math.ceil(ms/1000); return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0'); }
  return (ms/1000).toFixed(1) + 's';
}
var $ = function(id){ return document.getElementById(id); };

/* ---------- toast / copy ---------- */
var _toastT = null;
function toast(msg, ms){
  var t = $('toast'); t.textContent = msg; t.classList.add('up');
  clearTimeout(_toastT); _toastT = setTimeout(function(){ t.classList.remove('up'); }, ms || 1600);
}
function copyText(str){
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(str).then(function(){ toast('Copied'); }, function(){ _copyFallback(str); });
  } else { _copyFallback(str); }
}
function _copyFallback(str){
  var ta = document.createElement('textarea'); ta.value = str;
  ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta);
  ta.focus(); ta.select();
  try{ document.execCommand('copy'); toast('Copied'); }catch(e){ toast('Copy failed'); }
  document.body.removeChild(ta);
}

/* ---------- HTTP (spec §5) ---------- */
var BUSY_WHAT = { spinning:'the wheel is still spinning', result:'the result is still on screen', cooldown:'cooling down' };
function busyToast(d, verb){
  var secs = Math.max(1, Math.ceil((+(d && d.retry_in_ms) || 0) / 1000));
  var what = (d && d.error === 'bets_closed') ? 'bets are closed while the ball is rolling'
    : (BUSY_WHAT[d && d.state] || 'a game is running');
  toast((verb || 'Busy') + ' — ' + what + '. Try again in ' + secs + 's', 3000);
}
// Raw request: never toasts. {net, status, ok, d}
async function rawReq(method, path, body){
  var opt = { method: method };
  if(body !== undefined){ opt.headers = {'Content-Type':'application/json'}; opt.body = JSON.stringify(body); }
  var r, d = {};
  try{ r = await fetch(path, opt); }catch(e){ return { net: true, status: 0, ok: false, d: {} }; }
  try{ d = (await r.json()) || {}; }catch(e){ d = {}; }
  if(!isObj(d)) d = {};
  return { net: false, status: r.status, ok: r.ok && d.ok !== false, d: d };
}
async function api(method, path, body, verb){
  var opt = { method: method };
  if(body !== undefined){ opt.headers = {'Content-Type':'application/json'}; opt.body = JSON.stringify(body); }
  var r, d = {};
  try{ r = await fetch(path, opt); }
  catch(e){ toast('Network error — is Hexcast running?', 2400); return null; }
  try{ d = (await r.json()) || {}; }catch(e){ d = {}; }
  if(r.status === 409 || d.error === 'busy'){ busyToast(d, verb); return null; }
  if(!r.ok || d.ok === false){
    var msg = (typeof d.error === 'string' && d.error) || (typeof d.detail === 'string' && d.detail) || ('Request failed (HTTP ' + r.status + ')');
    toast(msg, 2600);
    return null;
  }
  return d;
}

async function loadClipList(){
  try{
    var d = await (await fetch('/api/list')).json();
    var seen = {}, dl = $('clip-list');
    dl.innerHTML = '';
    [['audio', d.audio || []], ['video', d.video || []]].forEach(function(g){
      g[1].forEach(function(name){
        if(seen[name]) return; seen[name] = 1;
        var o = document.createElement('option'); o.value = name; o.label = g[0];
        dl.appendChild(o);
      });
    });
  }catch(e){}
}

/* ---------- Hex display (DISPLAY_SPEC §1/§5 — "Hex does the math") ---------- */
// The helpers of the roulette tab's card and the craps tab's (window.GamesPage.display).
var DISPLAY_AMOUNT_MAX = 1e12;
// "+200", "-50", "−50", "1,000", "12.5" -> number; anything else -> null (it's text).
// Commas only as thousands separators: "1,5" is text, not 15.
function parseAmount(tok){
  var t = String(tok == null ? '' : tok).replace(/^−/, '-');
  if(t.indexOf(',') >= 0){
    if(!/^[+-]?\d{1,3}(,\d{3})+(\.\d*)?$/.test(t)) return null;
    t = t.replace(/,/g, '');
  }
  if(!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return null;
  var v = Number(t);
  return isFinite(v) ? v : null;
}
// One line each: "user amount text…" — the first word is the user (a leading @ is fine,
// "-" = no user), then an optional amount, then the text. A line needs a user or a text
// (else it is `skipped`); an amount the server would refuse — beyond ±1e12, or below 0
// when opts.signed is false (the craps board) — drops the line too (`bad`), rather than
// letting the server skip it without a word.
function parseDisplayLines(src, opts){
  var signed = !(opts && opts.signed === false);
  var lines = [], skipped = 0, bad = 0;
  String(src == null ? '' : src).split(/\r?\n/).forEach(function(raw){
    var w = raw.trim().split(/\s+/).filter(Boolean);
    if(!w.length) return;
    var line = {}, user = w[0].replace(/^@+/, '');
    if(user && user !== '-' && user !== '—') line.user = user;
    var rest = w.slice(1);
    if(rest.length){
      var a = parseAmount(rest[0]);
      if(a != null){
        if(Math.abs(a) > DISPLAY_AMOUNT_MAX || (a < 0 && !signed)){ bad++; return; }
        line.amount = a; rest = rest.slice(1);
      }
    }
    if(rest.length) line.text = rest.join(' ');
    if(line.user == null && line.text == null){ skipped++; return; }
    lines.push(line);
  });
  return { lines: lines, skipped: skipped, bad: bad, signed: signed };
}
function badAmountText(p){
  return p.signed === false ? 'amounts go from 0 to 1e12' : 'amounts go up to ±1e12';
}
function countText(p, max, what){
  var n = p.lines.length, bits = [n + ' ' + (what || 'line') + (n === 1 ? '' : 's')];
  if(n > max) bits.push('only the first ' + max + ' are sent');
  if(p.skipped) bits.push(p.skipped + ' skipped — a line needs a user or a text');
  if(p.bad) bits.push(p.bad + ' skipped — ' + badAmountText(p));
  return bits.join(' · ');
}
// The box has text but not one usable line: sending it would put up a "No winners" card
// (or an empty board) instead of what was typed — so nothing is sent. null = fine to send.
function refuseText(p, what){
  if(!p || p.lines.length || !(p.skipped || p.bad)) return null;
  var why = [];
  if(p.skipped) why.push('each line needs a user or a text');
  if(p.bad) why.push(badAmountText(p));
  return 'Nothing sent — no usable line (' + why.join('; ') + '). Empty the box to send ' +
    (what === 'board' ? 'an empty board' : 'a card with just the empty text') + '.';
}
function displayRefuse(host, msg){
  host.innerHTML = '<span class="bad">✕ ' + esc(msg) + '</span>';
}
// Raw POST for the display routes: never toasts. {net, status, ok, d}
function displayReq(path, body){ return rawReq('POST', path, body); }
// noun: 'spin' (roulette) or 'roll' (craps) — what the id field is called.
function displayError(res, noun){
  var d = res.d || {};
  noun = noun || 'spin';
  if(res.net) return 'Network error — is Hexcast running?';
  if(res.status === 409 && d.error === 'stale'){
    return 'Stale ' + noun + ' id — that ' + noun + ' is over' + (d.spin_id ? '; the current (or last) one is ' + d.spin_id : '') +
      '. Empty the ' + noun.charAt(0).toUpperCase() + noun.slice(1) + ' id field to use the latest ' + noun + '.';
  }
  if(res.status === 404 || res.status === 405 || d.error === 'unknown game') return "This Hexcast has no display API yet — restart it after updating.";
  var e = (typeof d.error === 'string' && d.error) || (typeof d.detail === 'string' && d.detail) || '';
  if(res.status === 400) return 'Not shown — ' + (e || 'the server rejected it');
  return e || ('Request failed (HTTP ' + res.status + ')');
}
// The exchange, briefly: the big STATE / TABLE in replies are trimmed (the socket has them).
function briefReply(d){
  if(!d || typeof d !== 'object') return d;
  var o = Object.assign({}, d);
  if(o.state && typeof o.state === 'object') o.state = '…';
  if(o.table && typeof o.table === 'object') o.table = { display_board: o.table.display_board, '…': '…' };
  return o;
}
function exchangeHTML(path, body, res){
  var q = 'POST ' + path + (body !== undefined ? '\n' + JSON.stringify(body) : '');
  var a = res.net ? 'no reply (network error)' : ('HTTP ' + res.status + '\n' + JSON.stringify(briefReply(res.d)));
  return '<pre class="dio"><span class="q">→ ' + esc(q) + '</span>\n← ' + esc(a) + '</pre>';
}
function displayOut(host, ok, msg, path, body, res){
  host.innerHTML = '<span class="' + (ok ? 'ok' : 'bad') + '">' + (ok ? '✓ ' : '✕ ') + esc(msg) + '</span>' + exchangeHTML(path, body, res);
}
function annSummary(a, ms){
  if(!a || typeof a !== 'object') return '';
  var n = Array.isArray(a.lines) ? a.lines.length : 0;
  return '“' + (a.title || 'WINNERS') + '” · ' + (n ? n + ' line' + (n === 1 ? '' : 's') : 'no lines — “' + (a.empty_text || 'No winners') + '”') +
    (ms != null ? ' · ' + fmtSec(ms) + ' left' : '') + (a.spin_id ? ' · for ' + a.spin_id : '');
}
function annLeft(a, at){
  if(!a || typeof a.expires_in_ms !== 'number' || !isFinite(a.expires_in_ms)) return null;
  return Math.max(0, a.expires_in_ms - (performance.now() - at));
}
// STATE.announce as it stands now: null once its countdown has run out (the server's
// clearing state is due any moment — or lost with the socket).
function annLive(a, at){
  if(!a || typeof a !== 'object' || Array.isArray(a)) return null;
  return annLeft(a, at) === 0 ? null : a;
}
var DISPLAY = { parseAmount: parseAmount, parseLines: parseDisplayLines, countText: countText, req: displayReq,
  error: displayError, out: displayOut, summary: annSummary, left: annLeft, live: annLive,
  refuseText: refuseText, refuse: displayRefuse };

window.GamesPage = Object.assign(window.GamesPage || {}, {
  esc: esc, toast: toast, copyText: copyText, loadClipList: loadClipList,
  api: api, rawReq: rawReq, busyToast: busyToast, display: DISPLAY
});
})();
