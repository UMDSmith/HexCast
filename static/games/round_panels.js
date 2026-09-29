/*
 * Hexcast Games — Russian Roulette + Trivia panel tabs      static/games/round_panels.js
 *
 * Builds section#tab-russian and section#tab-trivia of the Games panel (/games), one
 * tab each, next to Roulette and Craps:
 *   1. OBS browser source      both overlay URLs + Copy
 *   2. The game                live mirror (the real renderer, sound off), state + countdown,
 *                              start / test game / skip / stop / show / hide; Edit Mode ->
 *                              click -> placement & look editor (the craps / roulette modal:
 *                              sample game, drag, quick grid, look, branding, ▶ Preview,
 *                              Test in OBS = /preview, Save)
 *   3. Play as                 test a player by hand: bet, cash out, ride, answer (REAL coins
 *                              unless the game is a test game)
 *   4. Players                 everyone with coins in the game
 *   5. Lore (trivia)           the channel's own questions: list, add, import, delete
 *   6. History & stats · 7. Ledger (this game's events) · 8. Settings · 9. API
 *
 * Plain browser script, loaded after games_panel.html's inline script and after the
 * renderers (/static/games/russian.js, /static/games/trivia.js). The page's panel socket
 * reaches us through window.GamePanels.russian / .trivia (onConfig, onState, onStop,
 * onLedger, onLink, onTab); window.GamesPage.last lets us catch up if we load late.
 */
(function () {
  'use strict';

  var GP = window.GamesPage || {};
  function $(id) { return document.getElementById(id); }
  var esc = typeof GP.esc === 'function' ? GP.esc : function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var toast = typeof GP.toast === 'function' ? GP.toast : function (m) { console.log('[games]', m); };
  var copyText = typeof GP.copyText === 'function' ? GP.copyText : function (s) { try { navigator.clipboard.writeText(s); toast('Copied'); } catch (e) {} };
  function isObj(o) { return !!o && typeof o === 'object' && !Array.isArray(o); }
  function num(v, d) { v = parseFloat(v); return isFinite(v) ? v : d; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function fmt(n) { n = Math.round(+n || 0); return (n < 0 ? '−' : '') + Math.abs(n).toLocaleString(); }
  function signed(n) { n = Math.round(+n || 0); return (n > 0 ? '+' : '') + fmt(n); }
  function fmtTime(ts) { if (!ts) return ''; var d = new Date(ts * 1000); return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
  function editMode() { return document.body.classList.contains('edit-mode'); }
  function pick(o, keys) { var r = {}; keys.forEach(function (k) { if (o && o[k] !== undefined) r[k] = o[k]; }); return r; }
  function safe(fn) { try { return fn(); } catch (e) { console.error('[games] renderer error:', e); } }

  // Like the page's / craps' api(): toasts on failure, returns the body or null.
  // (A round game's /preview and /config never answer 409 busy.)
  async function api(method, path, body) {
    var opt = { method: method }, r, d = {};
    if (body !== undefined) { opt.headers = { 'Content-Type': 'application/json' }; opt.body = JSON.stringify(body); }
    try { r = await fetch(path, opt); } catch (e) { toast('Network error — is Hexcast running?', 2400); return null; }
    try { d = (await r.json()) || {}; } catch (e) { d = {}; }
    if (!isObj(d)) d = {};
    if (!r.ok || d.ok === false) {
      toast((typeof d.error === 'string' && d.error) || (typeof d.detail === 'string' && d.detail) ||
        ('Request failed (HTTP ' + r.status + ')'), 2600);
      return null;
    }
    return d;
  }

  // ---- stage views (a 1920x1080 stage scaled into a 16:9 box; the scene is BASE_W x BASE_H),
  //      the same as craps_panel.js' ----
  function makeView(box, stage, el, w, h) {
    var v = { box: box, stage: stage, el: el, inst: null, ro: null, fit: 0, scale: 1, sizeKey: null, sizeInst: null, rzTimer: 0 };
    el.style.width = w + 'px'; el.style.height = h + 'px';
    fitView(v);
    if (window.ResizeObserver) { v.ro = new ResizeObserver(function () { fitView(v); }); v.ro.observe(box); }
    return v;
  }
  function fitView(v) {
    var w = v.box.clientWidth;
    v.fit = w / 1920;                   // 0 while hidden (another tab): shown again = a new size
    if (!w) return;
    v.stage.style.transform = 'scale(' + v.fit + ')';
    // (a ResizeObserver callback runs after this frame's requestAnimationFrame callbacks -
    // the renderer's draw included - so the resize waits for the next task: the canvas it
    // reallocates is drawn again by the renderer's next frame before it is shown)
    clearTimeout(v.rzTimer);
    v.rzTimer = setTimeout(function () { resizeView(v); }, 0);
  }
  function placeView(v, x, y, scale) {
    v.el.style.left = x + '%';
    v.el.style.top = y + '%';
    v.el.style.transform = 'translate(-50%,-50%) scale(' + scale + ')';
    v.scale = scale;
    resizeView(v);
  }
  // The renderer's resize(), only when the box's on-screen size really changed (like
  // games_overlay.html's resizeSlot): a resize may reallocate its canvas, which blanks it,
  // so dragging (a move, not a new size) must never call it. The size is keyed by what
  // makes it - the stage's fit, the scale, the screen's pixel ratio - not by
  // getBoundingClientRect(), whose float noise (199.4999 / 199.5) flips a rounded size
  // while the box only moves. It runs right away - from an input event or a socket
  // message, before the next frame - so the renderer's own next frame redraws the new
  // canvas before it is shown (never a blank frame in between).
  function resizeView(v) {
    var inst = v.inst;
    if (!inst) return;
    var key = v.fit + '|' + v.scale + '|' + (window.devicePixelRatio || 1);
    if (key === v.sizeKey && inst === v.sizeInst) return;
    v.sizeKey = key; v.sizeInst = inst;
    safe(function () { inst.resize(); });
  }
  function viewMsg(el, text) { el.textContent = text || ''; el.style.display = text ? 'flex' : 'none'; }
  // The editor closes on a click on its backdrop only when the press started there too:
  // selecting text in an input and letting go outside the modal is not a click outside.
  // (Its buttons are looked up inside .modal-header / .modal-actions: the renderer's own DOM
  // in the preview comes first in the modal and may use the same class names - trivia's
  // footer has a span.test.)
  function closeOnBackdrop(ov, close) {
    var downOnOv = false;
    ov.addEventListener('pointerdown', function (e) { downOnOv = e.target === ov; });
    ov.addEventListener('click', function (e) { var d = downOnOv; downOnOv = false; if (e.target === ov && d) close(); });
  }

  var CSS = [
    '.rp-state{font:800 22px/1.1 var(--mono);color:var(--ink)} .rp-sub{color:var(--dim);font-size:13px;margin-top:2px}',
    '.rp-facts{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px;margin-top:12px;font-size:13px}',
    '.rp-facts .lbl{display:block;font:600 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}',
    '.rp-facts b{font-weight:700;overflow-wrap:anywhere}',
    '.rp-out{margin-top:10px;font:12.5px var(--mono);color:var(--dim);white-space:pre-wrap;min-height:18px}',
    '.rp-out.ok{color:var(--good)} .rp-out.err{color:var(--warn)}',
    '.rp-tag{font:700 10px/1 var(--mono);letter-spacing:.1em;padding:4px 7px;border-radius:6px;vertical-align:2px;margin-left:6px}',
    '.rp-tag.real{background:#3a1414;color:#ff8a80;border:1px solid #6b2020}',
    // Edit Mode: the same card chrome as roulette's #card-roulette / craps' #cr-card-table
    '.gwheel.rp-box.empty{border-radius:22px}',
    'body.edit-mode .rp-edit{background:#3a2a18;border-color:var(--amber);color:var(--amber-soft)}',
    'body.edit-mode .rp-card{border-color:var(--amber);cursor:pointer;position:relative}',
    'body.edit-mode .rp-card::after{content:"✎";position:absolute;top:12px;right:16px;color:var(--amber-soft);',
    '  text-shadow:0 1px 2px #000;font-size:16px}',
    'body.edit-mode .rp-card:hover{border-color:var(--amber-soft)}',
    '.rp-letters button{min-width:44px}',
    '.rp-lore{max-height:360px;overflow:auto}',
    '.rp-lore td{vertical-align:top}',
    '.rp-lore .q{color:var(--ink)} .rp-lore .a{color:var(--good);font-size:12px} .rp-lore .w{color:var(--dim);font-size:12px}',
    '.rp-bank{font-size:13px;color:var(--dim);margin-top:8px}',
    '.rp-bank b{color:var(--ink)} .rp-bank .err{color:var(--warn)}',
    '.rp-lscroll{max-height:320px;overflow:auto}',
    '.rp-stats{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--dim);margin-bottom:10px}',
    '.rp-stats b{color:var(--ink);font-size:15px}'
  ].join('\n');
  (function () { var s = document.createElement('style'); s.textContent = CSS; document.head.appendChild(s); })();

  // ======================================================================
  // per-game definitions
  // ======================================================================
  var PHASES = {
    russian: { idle: 'idle', betting: 'bets open', pulling: 'pulling the trigger', result: 'result', over: 'game over' },
    trivia: { idle: 'idle', betting: 'bets open', question: 'question open', votes: 'answers locked', reveal: 'reveal', over: 'game over' }
  };
  var THEMES = {
    russian: [['saloon', 'Saloon — wood & brass'], ['noir', 'Noir — black & white'], ['neon', 'Neon']],
    trivia: [['hex', 'Hex — Hexcast red'], ['gameshow', 'Game show — blue & gold'], ['neon', 'Neon']]
  };
  // The only keys a /preview's `overrides` may carry — and what the Edit-Mode editor edits
  // (= games.py APPEARANCE). LOOK = their defaults (games.py DEFAULTS): the editor's Reset.
  var APPEARANCE = {
    russian: ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'show_odds', 'sfx', 'sfx_volume'],
    trivia: ['x', 'y', 'scale', 'theme', 'title', 'lore_label', 'show_rules', 'show_players', 'players_max', 'sfx', 'sfx_volume']
  };
  var LOOK = {
    russian: { x: 50, y: 50, scale: 1, theme: 'saloon', title: 'Russian Roulette', show_rules: true, show_players: true,
      players_max: 8, show_odds: true, sfx: true, sfx_volume: 0.6 },
    trivia: { x: 50, y: 50, scale: 1, theme: 'hex', title: 'TRIVIA', lore_label: 'Channel Lore', show_rules: true,
      show_players: true, players_max: 8, sfx: true, sfx_volume: 0.5 }
  };
  var TEXT_MAX = { title: 32, lore_label: 24 };                        // games.py ROUND_TITLE_MAX / LORE_LABEL_MAX
  // The game card's own text fields, in characters (code points, like games.py - no
  // maxlength, which counts UTF-16 units: an emoji is 2): the dummy's name (DUMMY_NAME_MAX)
  // and a lore question's category (_qtext(category, 60)).
  var FIELD_MAX = { dummy: 24, lcat: 60, duser: 40, pu: 40, lq: 300, lc: 120 };
  var BASE = { russian: [1100, 560], trivia: [1120, 630] };            // the renderers' BASE_W x BASE_H (if not loaded)
  // [key, label, kind, extra, title]
  var SETTINGS = {
    russian: [
      ['rounds', 'Pulls per game', 'int', [1, 5], 'Round k loads k bullets: pull 1 = 1/6, pull 2 = 2/6 …'],
      ['house_edge_pct', 'House edge %', 'num', [0, 25]],
      ['open_bet_seconds', 'First bet window (s)', 'num', [5, 300]],
      ['between_seconds', 'Window before later pulls (s)', 'num', [5, 300]],
      ['pull_seconds', 'Pull animation (s)', 'num', [6, 20]],
      ['result_seconds', 'Result on screen (s)', 'num', [2, 30]],
      ['summary_seconds', 'Game-over card (s)', 'num', [4, 60]],
      ['min_bet', 'Min bet', 'int', [1, 1e9]],
      ['max_bet', 'Max bet per pull (0 = none)', 'int', [0, 1e12]],
      ['max_payout', 'Max survive payout (0 = none)', 'int', [0, 1e12], 'A survive stake worth this much is cashed out'],
      ['volunteer_cut_pct', 'Volunteer cut %', 'num', [0, 50], 'Of the bank\'s net win, paid to the dummy\'s user'],
      ['dummy_name', 'Default dummy name', 'text', 24],
      ['currency', 'Currency', 'text', 24],
      ['commands_text', 'Commands line on the overlay', 'text', 120, 'Your bot\'s commands, e.g. !live 100 · !bang 50 · !cashout'],
      ['title', 'Title on the overlay', 'text', 32, 'Your branding: the name in the overlay\'s header (Edit Mode can preview it)'],
      ['hide_when_idle', 'Hide between games', 'bool'],
      ['sfx', 'Overlay sound effects', 'bool'],
      ['sfx_volume', 'Sound volume', 'num', [0, 1]],
      ['pull_clip', 'Soundboard clip: pull', 'clip'],
      ['click_clip', 'Soundboard clip: click', 'clip'],
      ['bang_clip', 'Soundboard clip: bang', 'clip']
    ],
    trivia: [
      ['questions', 'Questions per game', 'int', [1, 50]],
      ['difficulty', 'Difficulty', 'select', [['ramp', 'Ramp: easy → medium → hard'], ['easy', 'All easy'], ['medium', 'All medium'], ['hard', 'All hard'], ['mixed', 'Mixed at random']]],
      ['category', 'OpenTDB category', 'category'],
      ['lore', 'Your own questions (lore)', 'select', [['mixed', 'Mixed in'], ['only', 'Lore only'], ['off', 'Off']]],
      ['lore_every', 'Mixed: every Nth question is lore', 'int', [1, 50]],
      ['repeat_hours', 'Don\'t repeat a question for (hours, 0 = never)', 'num', [0, 8760]],
      ['open_bet_seconds', 'First bet window (s)', 'num', [5, 300]],
      ['between_seconds', 'Window between questions (s)', 'num', [5, 300]],
      ['answer_seconds', 'Answer window (s)', 'num', [5, 120]],
      ['show_votes', 'Show the votes', 'select', [['before_reveal', 'Right before the answer'], ['live', 'Live while answering'], ['off', 'Never']]],
      ['votes_seconds', 'Votes on screen (s)', 'num', [1, 30]],
      ['reveal_seconds', 'Answer on screen (s)', 'num', [2, 30]],
      ['summary_seconds', 'Game-over card (s)', 'num', [4, 60]],
      ['pay_easy', 'Pays: easy (×, total)', 'num', [1, 100]],
      ['pay_medium', 'Pays: medium (×)', 'num', [1, 100]],
      ['pay_hard', 'Pays: hard (×)', 'num', [1, 100]],
      ['streak_bonus_pct', 'Streak bonus % per question', 'num', [0, 100]],
      ['max_multiplier', 'Max × the coins put in (0 = none)', 'num', [0, 1e6]],
      ['min_bet', 'Min bet', 'int', [1, 1e9]],
      ['max_bet', 'Max bet (0 = none)', 'int', [0, 1e12]],
      ['currency', 'Currency', 'text', 24],
      ['commands_text', 'Commands line on the overlay', 'text', 120, 'Your bot\'s commands, e.g. !bet 100 · !a B · !ride · !cashout'],
      ['title', 'Title on the overlay', 'text', 32, 'Your branding: the board\'s name (header + the card between games)'],
      ['lore_label', 'Name of your own questions', 'text', 24, 'What the overlay calls your lore questions, e.g. "Channel Lore"'],
      ['hide_when_idle', 'Hide between games', 'bool'],
      ['sfx', 'Overlay sound effects', 'bool'],
      ['sfx_volume', 'Sound volume', 'num', [0, 1]],
      ['question_clip', 'Soundboard clip: question', 'clip'],
      ['reveal_clip', 'Soundboard clip: reveal', 'clip']
    ]
  };
  var REASONS = { bet: 'bet', add: 'added to bet', win: 'win', cashout: 'cash out', refund: 'refund / taken back',
    volunteer_cut: 'volunteer cut' };

  // ======================================================================
  // one tab
  // ======================================================================
  function Tab(key, title) {
    this.key = key;
    this.title = title;
    this.host = $('tab-' + key);
    this.api = '/games/api/' + key;
    this.cfg = {};
    this.st = null; this.stAt = 0;
    this.mir = null;               // the live mirror's view (mir.inst = its renderer)
    this.mfailed = false;          // the renderer is missing / create() threw: a placeholder box
    this.ed = null;                // the open placement & look editor: {refresh, close}
    this.ledger = []; this.lseen = {}; this.lastSeq = 0; this.lbooted = false;
    this.stats = null;
    if (!this.host) return;
    this.build();
    var self = this;
    setInterval(function () { if (self.shown()) self.tick(); }, 250);
  }
  var T = Tab.prototype;
  T.shown = function () { return this.host.classList.contains('sel'); };
  T.R = function () { var R = window.HexGames && window.HexGames[this.key]; return R && typeof R.create === 'function' ? R : null; };
  T.baseW = function () { var R = this.R(); return (R && +R.BASE_W) || BASE[this.key][0]; };
  T.baseH = function () { var R = this.R(); return (R && +R.BASE_H) || BASE[this.key][1]; };
  // Saved config with every key filled in (the renderer's defaults, then the look's).
  T.cfgFull = function () { var R = this.R(); return Object.assign({}, R && R.DEFAULTS, LOOK[this.key], this.cfg); };
  T.id = function (s) { return 'rp-' + this.key + '-' + s; };
  T.$ = function (s) { return $(this.id(s)); };

  T.req = async function (method, path, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    try {
      var r = await fetch(path, opts);
      var d = {}; try { d = await r.json(); } catch (e) {}
      return { ok: r.ok && d.ok !== false, status: r.status, d: d };
    } catch (e) { return { ok: false, status: 0, d: { error: 'offline: ' + e.message } }; }
  };
  T.act = async function (path, body, okMsg, outEl) {
    var res = await this.req('POST', this.api + path, body || {});
    var out = outEl || this.$('out');
    if (res.ok) {
      if (res.d.state) this.onState(res.d.state);
      if (out) { out.className = 'rp-out ok'; out.textContent = okMsg ? (typeof okMsg === 'function' ? okMsg(res.d) : okMsg) : 'ok'; }
      else if (okMsg) toast(typeof okMsg === 'function' ? okMsg(res.d) : okMsg);
    } else {
      var msg = res.d.error || ('HTTP ' + res.status);
      if (res.d.retry_in_ms != null) msg += ' (' + Math.ceil(res.d.retry_in_ms / 1000) + 's left in this phase)';
      if (out) { out.className = 'rp-out err'; out.textContent = msg; } else toast(msg);
    }
    return res;
  };

  // ---------------------------------------------------------------- markup
  T.build = function () {
    var k = this.key, id = this.id.bind(this), rr = k === 'russian';
    var base = location.protocol + '//' + location.host + '/games/overlay';
    var controls = rr
      ? '<div class="grid" style="grid-template-columns:1fr 1fr;gap:9px;margin-top:14px">' +
        '<label class="f">Dummy name <input id="' + id('dummy') + '" placeholder="Dummy" autocomplete="off"></label>' +
        '<label class="f">Volunteer (gets the cut) <input id="' + id('duser') + '" placeholder="chatter name" autocomplete="off"></label></div>' +
        '<div class="row"><button class="act" id="' + id('start') + '">Start game</button>' +
        '<button class="sec" id="' + id('test') + '" title="Plays exactly the same, but writes nothing to the ledger">Test game</button>' +
        '<button class="sec" id="' + id('setdummy') + '" title="Change the dummy (before the first pull, or for the next game)">Set dummy</button></div>'
      : '<div class="grid" style="grid-template-columns:1fr 1fr;gap:9px;margin-top:14px">' +
        '<label class="f">Questions <input type="number" id="' + id('nq') + '" min="1" max="50" placeholder="15"></label>' +
        '<label class="f">Lore <select id="' + id('lorem') + '"><option value="">as in settings</option><option value="mixed">mixed in</option><option value="only">lore only</option><option value="off">off</option></select></label></div>' +
        '<div class="row"><button class="act" id="' + id('start') + '">Start game</button>' +
        '<button class="sec" id="' + id('test') + '" title="Plays exactly the same, but writes nothing to the ledger">Test game</button></div>' +
        '<div class="rp-bank" id="' + id('bank') + '">question bank: …</div>';
    var play = rr
      ? '<div class="grid" style="grid-template-columns:1fr 1fr 1fr;gap:9px">' +
        '<label class="f">User <input id="' + id('pu') + '" placeholder="viewer" autocomplete="off"></label>' +
        '<label class="f">Side <select id="' + id('ps') + '"><option value="survive">survive (it clicks)</option><option value="bang">bang (it fires)</option></select></label>' +
        '<label class="f">Amount <input type="number" id="' + id('pa') + '" min="1" step="1" placeholder="coins"></label></div>' +
        '<div class="row"><button class="act" id="' + id('bet') + '">Bet</button><button class="sec" id="' + id('cash') + '">Cash out</button>' +
        '<button class="sec" id="' + id('back') + '" title="Take back what went down in this betting window">Take back</button></div>'
      : '<div class="grid" style="grid-template-columns:1fr 1fr;gap:9px">' +
        '<label class="f">User <input id="' + id('pu') + '" placeholder="viewer" autocomplete="off"></label>' +
        '<label class="f">Amount <input type="number" id="' + id('pa') + '" min="1" step="1" placeholder="coins"></label></div>' +
        '<div class="row"><button class="act" id="' + id('bet') + '">Bet</button><button class="sec" id="' + id('ride') + '">Ride</button>' +
        '<button class="sec" id="' + id('cash') + '">Cash out</button></div>' +
        '<div class="row rp-letters" id="' + id('letters') + '"><span class="note">Answer:</span>' +
        ['A', 'B', 'C', 'D', 'E'].map(function (l) { return '<button class="sec" data-a="' + l + '">' + l + '</button>'; }).join('') + '</div>';
    var lore = rr ? '' :
      '<div class="card"><h2><span id="' + id('lorename') + '">' + esc(LOOK.trivia.lore_label) + '</span> <span class="pill" id="' + id('lorecount') + '">…</span></h2>' +
      '<p class="hint">Your own questions (the lore), mixed into games (or a lore-only game). 2–5 options: the right answer plus 1–4 wrong ones. A question isn\'t asked again for <b>Don\'t repeat</b> hours. The overlay calls them by <b>Name of your own questions</b> (Settings, or Edit Mode); a question without a category shows that name as its category.</p>' +
      '<div class="grid" style="grid-template-columns:2fr 1fr;gap:9px">' +
      '<label class="f">Question <input id="' + id('lq') + '" placeholder="What colour is the streamer\'s hat?"></label>' +
      '<label class="f">Right answer <input id="' + id('lc') + '" placeholder="Red"></label>' +
      '<label class="f">Wrong answers (one per line, 1–4) <textarea id="' + id('lw') + '" rows="3" placeholder="Blue&#10;Green&#10;Gold"></textarea></label>' +
      '<div style="display:flex;flex-direction:column;gap:9px"><label class="f">Difficulty <select id="' + id('ld') + '"><option value="easy">easy</option><option value="medium" selected>medium</option><option value="hard">hard</option></select></label>' +
      '<label class="f">Category <input id="' + id('lcat') + '" placeholder="' + esc(LOOK.trivia.lore_label) + '"></label></div></div>' +
      '<div class="row"><button class="act" id="' + id('ladd') + '">Add question</button><span class="rp-out" id="' + id('lout') + '"></span></div>' +
      '<details style="margin-top:10px"><summary class="note">Import JSON</summary><textarea id="' + id('limp') + '" rows="5" style="margin-top:8px" placeholder=\'[{"question":"…","correct":"…","incorrect":["…","…"],"difficulty":"easy"}]\'></textarea>' +
      '<div class="row"><button class="sec" id="' + id('limpgo') + '">Import</button></div></details>' +
      '<div class="rp-lore" style="margin-top:12px"><table class="tb" id="' + id('lorelist') + '"></table></div></div>';

    this.host.innerHTML =
      '<div class="card"><h2>OBS browser source</h2><p class="hint">The first URL shows every game in one source; the second only ' + esc(this.title) + '. It stays transparent between games unless <b>Hide between games</b> is off (or you press Show).</p>' +
      '<div class="url"><a id="' + id('u1') + '" target="_blank" rel="noopener">' + esc(base) + '</a><button class="sec" id="' + id('c1') + '">Copy</button></div>' +
      '<div class="url"><a id="' + id('u2') + '" target="_blank" rel="noopener">' + esc(base + '?game=' + k) + '</a><button class="sec" id="' + id('c2') + '">Copy</button></div></div>' +

      '<div class="card rp-card" id="' + id('card') + '"><h2>' + esc(this.title) + ' <span class="pill" id="' + id('pill') + '">connecting…</span></h2>' +
      '<p class="hint">' + (rr
        ? 'Live mirror of the overlay. A revolver and a stuffed dummy: pull k loads k bullets and re-spins; chat bets the dummy <b>survives</b> (rides and grows) or goes <b>bang</b> this pull. Every bet is against the bank (your bot). The server spins with a cryptographic RNG — nothing here can steer it.'
        : 'Live mirror of the overlay. Bet before you see the question, then answer. Questions come from Open Trivia DB and your own lore. Winners ride or cash out between questions. Every bet is against the bank (your bot).') +
      ' Turn on <b>Edit Mode</b> and click this card to place and style the ' + (rr ? 'scene' : 'board') + '.</p>' +
      '<div class="game"><div class="mirror" id="' + id('mirror') + '"><div class="gstage" id="' + id('stage') + '"><div class="gwheel rp-box off" id="' + id('box') + '"></div></div>' +
      '<span class="mtag" id="' + id('tag') + '">hidden</span><div class="pmsg" id="' + id('msg') + '" style="display:none"></div></div>' +
      '<div class="gside"><div class="kv">State</div><div class="rp-state" id="' + id('state') + '">—</div><div class="rp-sub" id="' + id('sub') + '"></div>' +
      '<div class="rp-facts" id="' + id('facts') + '"></div>' + controls +
      '<div class="row"><button class="sec" id="' + id('skip') + '" title="End the current phase now (close bets, pull, next question…)">Skip ▶</button>' +
      '<button class="sec" id="' + id('stop') + '" title="End the game now: stakes still on the line are refunded, rides cashed out">Stop game</button>' +
      '<button class="sec" id="' + id('show') + '">Show</button><button class="sec" id="' + id('hide') + '">Hide</button>' +
      '<button class="sec edit-only rp-edit" id="' + id('edit') + '">✎ Edit placement</button></div>' +
      '<div class="rp-out" id="' + id('out') + '"></div>' +
      '</div></div></div>' +

      '<div class="two"><div class="card"><h2>Play as <span class="rp-tag real">real coins</span></h2>' +
      '<p class="hint">Test a player by hand — exactly what your bot does through the API. Real ledger debits / credits unless the game is a <b>test game</b>; use test names.</p>' +
      play + '<div class="rp-out" id="' + id('pout') + '"></div></div>' +
      '<div class="card"><h2>Players <span class="pill" id="' + id('ppill') + '">—</span></h2><div class="tscroll"><table class="tb" id="' + id('players') + '"></table></div></div></div>' +
      lore +
      '<div class="card"><h2>History &amp; stats</h2><div class="rp-stats" id="' + id('stats') + '"></div><div class="tscroll"><table class="tb" id="' + id('hist') + '"></table></div>' +
      '<div class="row"><button class="sec" id="' + id('hclear') + '">Clear history &amp; stats</button></div></div>' +
      '<div class="card"><h2>Ledger</h2><p class="hint">This game\'s coin movements (the games ledger is shared: seq gaps are other games). <b>debit</b> = the bank takes a stake, <b>credit</b> = it pays (a win, a cash-out, a refund' + (rr ? ', the volunteer\'s cut' : '') + '). Test games never appear here.</p>' +
      '<div class="rp-lscroll"><table class="lg" id="' + id('ledger') + '"></table></div></div>' +
      '<div class="card"><h2>Settings <span class="note" id="' + id('dirty') + '"></span></h2>' +
      '<p class="hint">Timing, money rules, branding and sounds for ' + esc(this.title) + '. Placement and look live in <b>Edit Mode</b> (click the ' + esc(this.title) + ' card). Soundboard clips fire server-side on the soundboard overlay — keep that browser source in the scene too.</p>' +
      '<div class="grid" id="' + id('settings') + '"></div>' +
      '<div class="row"><button class="act" id="' + id('save') + '">Save settings</button><button class="sec" id="' + id('revert') + '">Revert</button></div></div>' +
      '<div class="card"><h2>API</h2><p class="hint">Hexcast never reads chat: your bot turns chat commands into these calls. Full reference: <code>docs/' + (rr ? 'russian_roulette' : 'trivia') + '.md</code>.</p>' +
      '<table class="api">' + this.apiRows() + '</table><h3>Examples</h3><pre>' + esc(this.examples()) + '</pre></div>';

    this.wire();
    this.buildSettings();
  };

  T.apiRows = function () {
    var a = '/games/api/' + this.key, rows;
    if (this.key === 'russian') rows = [
      ['POST ' + a + '/start', '{dummy, dummy_user, rounds, seconds, test} — start a game (409 if one is running)'],
      ['POST ' + a + '/bet', '{user, side: survive|bang, amount} — ledger debit; survive rides from pull to pull, bang is this pull only'],
      ['POST ' + a + '/cashout', '{user} — credit the survive stake\'s current value (bets open only)'],
      ['POST ' + a + '/remove', '{user, side?} — take back what went down in this window (refund)'],
      ['POST ' + a + '/dummy', '{dummy, dummy_user} — the dummy (before pull 1, or the next game)'],
      ['POST ' + a + '/next', 'end the current phase now (alias /pull)'],
      ['GET ' + a + '/table', 'the STATE: phase, round, odds, players, last pull, summary'],
      ['GET ' + a + '/user/{name}', 'one player + their session totals'],
      ['GET ' + a + '/ledger?since=0', 'this game\'s ledger events (tail it by seq and pay them)'],
      ['POST ' + a + '/stop', 'end the game: stakes refunded, rides cashed out'],
      ['POST ' + a + '/preview', '{overrides, seconds} — Test in OBS (Edit Mode): on screen for a few seconds with that look; never touches the game (/preview/clear ends it)'],
      ['GET ' + a + '/history · /bets · /validate', 'finished games + stats · rules + odds · dry-run a bet']
    ];
    else rows = [
      ['POST ' + a + '/start', '{questions, difficulty, category, lore, seconds, test} — start a game (fetches question 1 first; 503 if none)'],
      ['POST ' + a + '/bet', '{user, amount} — a stake on the NEXT question (bets open only)'],
      ['POST ' + a + '/answer', '{user, answer: A-E | 1-5 | text} — anyone; last answer counts; only bettors are paid'],
      ['POST ' + a + '/ride', '{user} — a winner lets the whole balance ride'],
      ['POST ' + a + '/cashout', '{user} — a winner (or a fresh bet) takes the balance (alias /remove)'],
      ['POST ' + a + '/next', 'end the current phase now'],
      ['GET ' + a + '/table', 'the STATE (the answer is only in it from the reveal on)'],
      ['GET|POST ' + a + '/lore', 'your own questions (lore): list · add {question, correct, incorrect[], difficulty, category} or {questions:[…]}'],
      ['POST ' + a + '/lore/remove', '{id} or {ids:[…]}'],
      ['GET ' + a + '/bank · /categories', 'question pool + asked list · OpenTDB categories'],
      ['POST ' + a + '/asked/clear', '"new night": questions already asked may come back'],
      ['GET ' + a + '/ledger?since=0', 'this game\'s ledger events'],
      ['POST ' + a + '/stop', 'end the game: stakes refunded, winnings cashed out'],
      ['POST ' + a + '/preview', '{overrides, seconds} — Test in OBS (Edit Mode): on screen for a few seconds with that look; never touches the game (/preview/clear ends it)']
    ];
    return rows.map(function (r) { return '<tr><td>' + esc(r[0]) + '</td><td>' + esc(r[1]) + '</td></tr>'; }).join('');
  };
  T.examples = function () {
    var h = 'http://' + location.host + '/games/api/' + this.key;
    if (this.key === 'russian') return [
      'curl -X POST ' + h + '/start -H "Content-Type: application/json" -d "{\\"dummy\\":\\"Bob\\",\\"dummy_user\\":\\"bob\\"}"',
      'curl "' + h + '/bet?user=alice&side=survive&amount=100"',
      'curl "' + h + '/bet?user=carl&side=bang&amount=50"',
      'curl "' + h + '/cashout?user=alice"',
      'curl "' + h + '/ledger?since=0"'].join('\n');
    return [
      'curl -X POST ' + h + '/start',
      'curl "' + h + '/bet?user=alice&amount=100"',
      'curl "' + h + '/answer?user=alice&answer=B"',
      'curl "' + h + '/ride?user=alice"',
      'curl "' + h + '/cashout?user=alice"',
      'curl -X POST ' + h + '/lore -H "Content-Type: application/json" -d "{\\"question\\":\\"What colour is the streamer\'s hat?\\",\\"correct\\":\\"Red\\",\\"incorrect\\":[\\"Blue\\",\\"Green\\"],\\"difficulty\\":\\"easy\\"}"'].join('\n');
  };

  // ---------------------------------------------------------------- wiring
  T.wire = function () {
    var self = this, $$ = this.$.bind(this), k = this.key;
    var base = location.protocol + '//' + location.host + '/games/overlay';
    $$('u1').href = base; $$('u2').href = base + '?game=' + k;
    $$('c1').onclick = function () { copyText(base); };
    $$('c2').onclick = function () { copyText(base + '?game=' + k); };
    // the card's text fields cut to FIELD_MAX characters, like the Settings text fields
    Object.keys(FIELD_MAX).forEach(function (f) {
      var el = $$(f);
      if (!el) return;
      el.addEventListener('input', function (e) { if (!e.isComposing) limitText(el, FIELD_MAX[f]); });
      el.addEventListener('change', function () { limitText(el, FIELD_MAX[f]); });
    });
    function field(f) { return cut($$(f).value.trim(), FIELD_MAX[f]).trim(); }
    function startBody(test) {
      var b = { test: !!test };
      if (k === 'russian') {
        var dn = field('dummy'), du = $$('duser').value.trim();
        if (dn) b.dummy = dn;
        if (du) b.dummy_user = du;
      } else {
        var n = parseInt($$('nq').value, 10), lm = $$('lorem').value;
        if (n > 0) b.questions = n;
        if (lm) b.lore = lm;
      }
      return b;
    }
    $$('start').onclick = function () {
      $$('out').className = 'rp-out'; $$('out').textContent = k === 'trivia' ? 'getting question 1…' : '';
      self.act('/start', startBody(false), 'Game started — bets are open');
    };
    $$('test').onclick = function () { self.act('/start', startBody(true), 'Test game started — no coins move'); };
    if ($$('setdummy')) $$('setdummy').onclick = function () {
      self.act('/dummy', { dummy: field('dummy'), dummy_user: $$('duser').value.trim() }, function (d) { return 'Dummy set for ' + d.applies; });
    };
    $$('skip').onclick = function () { self.act('/next', {}, function (d) { return 'Skipped ' + d.skipped; }); };
    $$('stop').onclick = function () {
      if (!confirm('Stop the ' + self.title + ' game? Stakes still on the line are refunded and rides are cashed out.')) return;
      self.act('/stop', {}, 'Stopped');
    };
    $$('show').onclick = function () { self.act('/show', {}, 'Shown'); };
    $$('hide').onclick = function () { self.act('/hide', {}, 'Hidden'); };
    // play as
    var pout = $$('pout');
    function who() { return $$('pu').value.trim(); }
    $$('bet').onclick = function () {
      var b = { user: who(), amount: $$('pa').value };
      if (k === 'russian') b.side = $$('ps').value;
      self.act('/bet', b, function (d) { return 'Bet ' + fmt(d.amount) + (d.side ? ' on ' + d.side : '') + ' (debit #' + ((d.debits || [])[0] || {}).seq + ')'; }, pout);
    };
    $$('cash').onclick = function () {
      self.act('/cashout', { user: who() }, function (d) { var c = (d.credits || [])[0]; return c ? 'Cashed out ' + fmt(c.amount) : 'Nothing to pay'; }, pout);
    };
    if ($$('back')) $$('back').onclick = function () {
      self.act('/remove', { user: who() }, function (d) { var c = (d.credits || [])[0]; return c ? 'Took back ' + fmt(c.amount) : 'ok'; }, pout);
    };
    if ($$('ride')) $$('ride').onclick = function () { self.act('/ride', { user: who() }, 'Riding', pout); };
    if ($$('letters')) $$('letters').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-a]'); if (!b) return;
      self.act('/answer', { user: who(), answer: b.dataset.a }, function (d) { return 'Answered ' + d.answer + (d.bettor ? '' : ' (not a bettor: not paid)'); }, pout);
    });
    // lore
    if (k === 'trivia') {
      $$('ladd').onclick = async function () {
        var body = { question: $$('lq').value, correct: $$('lc').value, incorrect: $$('lw').value.split(/\n|\|/).map(function (s) { return s.trim(); }).filter(Boolean),
          difficulty: $$('ld').value, category: field('lcat') || undefined };
        var r = await self.req('POST', self.api + '/lore', body);
        $$('lout').className = 'rp-out ' + (r.ok ? 'ok' : 'err');
        $$('lout').textContent = r.ok ? 'Added' : (r.d.error || 'failed');
        if (r.ok) { $$('lq').value = ''; $$('lc').value = ''; $$('lw').value = ''; self.loadLore(); }
      };
      $$('limpgo').onclick = async function () {
        var raw; try { raw = JSON.parse($$('limp').value); } catch (e) { $$('lout').className = 'rp-out err'; $$('lout').textContent = 'Not valid JSON'; return; }
        var r = await self.req('POST', self.api + '/lore', { questions: Array.isArray(raw) ? raw : (raw.questions || [raw]) });
        $$('lout').className = 'rp-out ' + (r.ok ? 'ok' : 'err');
        $$('lout').textContent = r.ok ? 'Imported ' + r.d.added.length + (r.d.rejected.length ? ', ' + r.d.rejected.length + ' rejected' : '') : (r.d.error || 'failed');
        if (r.ok) self.loadLore();
      };
      $$('lorelist').addEventListener('click', async function (e) {
        var b = e.target.closest('button[data-del]'); if (!b) return;
        if (!confirm('Delete this lore question?')) return;
        var r = await self.req('POST', self.api + '/lore/remove', { id: b.dataset.del });
        if (r.ok) self.loadLore(); else toast(r.d.error || 'failed');
      });
      this.$('bank').addEventListener('click', async function (e) {
        if (!e.target.closest('button[data-clear]')) return;
        if (!confirm('Start a "new night"? Questions already asked may be asked again.')) return;
        var r = await self.req('POST', self.api + '/asked/clear');
        if (r.ok) { toast('Cleared ' + r.d.cleared + ' asked questions'); self.renderBank(r.d); }
      });
    }
    $$('hclear').onclick = async function () {
      if (!confirm('Clear the ' + self.title + ' history and stats?')) return;
      var r = await self.req('POST', self.api + '/history/clear');
      if (r.ok) { self.stats = r.d; self.renderHistory(); }
    };
    // settings
    $$('save').onclick = function () { self.saveSettings(); };
    $$('revert').onclick = function () { self.fillSettings(true); };
    // Edit Mode: the card (or its ✎ Edit placement button) opens the placement & look editor
    $$('edit').addEventListener('click', function (e) { e.stopPropagation(); self.openEditor(); });
    $$('card').addEventListener('click', function (e) {
      if (!editMode()) return;
      if (e.target.closest('button, a, input, select, textarea, label, .pill')) return;
      self.openEditor();
    });
    // the live mirror (non-interactive: .mirror .gwheel has no pointer events)
    this.mir = makeView($$('mirror'), $$('stage'), $$('box'), this.baseW(), this.baseH());
    if (!window.ResizeObserver) window.addEventListener('resize', function () { fitView(self.mir); });
  };

  // ---------------------------------------------------------------- mirror
  // Saved config, plus the look of a Test in OBS (STATE.preview) while it is on screen —
  // what the overlay shows (like craps' mirrorEffective with a test roll's overrides).
  T.mirrorEffective = function () {
    var c = this.cfgFull(), pv = this.st && this.st.preview;
    if (isObj(pv) && isObj(pv.overrides)) {
      APPEARANCE[this.key].forEach(function (k) { if (pv.overrides[k] != null) c[k] = pv.overrides[k]; });
    }
    return c;
  };
  T.applyMirror = function () {
    var v = this.mir, R = this.R(), c = this.mirrorEffective();
    if (!v) return;
    if (!v.inst && !this.mfailed) {
      if (!R) {
        this.mfailed = true;
        viewMsg(this.$('msg'), this.title + ' renderer not loaded (/static/games/' + this.key + '.js).');
      } else {
        try { v.inst = R.create(v.el, c, { sound: false }) || null; this._ck = JSON.stringify(c); }
        catch (e) {
          console.error('[games] ' + this.key + ' renderer create failed:', e);
          v.inst = null; this.mfailed = true;
          viewMsg(this.$('msg'), this.title + ' renderer failed to start: ' + (e && e.message || e));
        }
        if (v.inst && this.st) safe(function () { v.inst.setState(this.liveState()); }.bind(this));   // config came after a STATE
      }
      if (!v.inst) v.el.classList.add('empty');
    } else if (v.inst) {
      var key = JSON.stringify(c);
      if (key !== this._ck) { this._ck = key; safe(function () { v.inst.setConfig(c); }); }
    }
    // only when it moved: a resize re-allocates the renderer's canvas (every STATE comes through here)
    var pk = num(c.x, 50) + '|' + num(c.y, 50) + '|' + num(c.scale, 1);
    if (pk !== this._pk) { this._pk = pk; placeView(v, num(c.x, 50), num(c.y, 50), num(c.scale, 1)); }
  };
  T.liveState = function () {
    var s = Object.assign({}, this.st), dt = performance.now() - this.stAt;
    if (isObj(s.game)) {
      var g = Object.assign({}, s.game);
      if (typeof g.ends_in_ms === 'number') g.ends_in_ms = Math.max(0, g.ends_in_ms - dt);
      if (typeof g.elapsed_ms === 'number') g.elapsed_ms += dt;
      s.game = g;
    }
    return s;
  };

  // ---------------------------------------------------------------- socket hooks
  T.onConfig = function (config) {
    var c = isObj(config) && isObj(config[this.key]) ? config[this.key] : null;
    if (!c) return;
    this.cfg = c;
    this.fillSettings(false);
    this.applyMirror();
    if (this.ed) this.ed.refresh();
    if (this.key === 'trivia') {        // the panel names the lore the way the overlay does
      var name = String(this.cfgFull().lore_label || LOOK.trivia.lore_label);
      this.$('lorename').textContent = name;
      this.$('lcat').placeholder = name;
    }
  };
  T.onState = function (st) {
    if (!isObj(st)) return;
    this.st = st; this.stAt = performance.now();
    this.applyMirror();
    var inst = this.mir && this.mir.inst;
    if (inst) safe(function () { inst.setState(this.liveState()); }.bind(this));
    this.$('box').classList.toggle('off', !st.visible);
    var tag = this.$('tag'); tag.textContent = st.visible ? 'on stream' : 'hidden'; tag.classList.toggle('on', !!st.visible);
    this.render();
    var hk = st.last && st.last.id;
    if (hk !== this._hk) { this._hk = hk; this.loadHistory(); }
  };
  T.onStop = function (game) { if (!game || game === this.key) this.render(); };
  T.onLink = function (on) {
    var p = this.$('pill'); p.textContent = on ? 'live' : 'offline — reconnecting'; p.className = 'pill ' + (on ? 'on' : 'off');
    if (on && this.lbooted) this.fetchLedger(this.lastSeq);
  };
  T.onLedger = function (events) { this.addEvents(events); this.renderLedger(); };
  T.onTab = function (key) {
    if (key !== this.key) return;
    if (this.mir && !window.ResizeObserver) fitView(this.mir);   // ResizeObserver refits it on its own
    if (!this.lbooted) { this.lbooted = true; this.fetchLedger(0); this.loadHistory(); this.loadClips(); if (this.key === 'trivia') { this.loadLore(); this.loadBank(); this.loadCategories(); } }
  };

  // ======================================================================
  // Edit Mode: placement & look editor (roulette's / craps' openEditor(), same modal)
  // ======================================================================
  // The round renderers have no play() / setTable(): they draw a STATE. So the editor
  // feeds its own renderer a sample game in its betting window (players, rules, odds),
  // ▶ Preview steps a local demo round through setState(), and Test in OBS is the
  // server's /preview (the look on the real overlay for a few seconds, not saved).

  // rr_odds() of games.py (display only: the sample game's payout ladder)
  function rrOdds(rounds, edgePct) {
    var keep = 1 - clamp(num(edgePct, 5), 0, 25) / 100, out = [];
    function alive(c) { var p = 1; for (var i = 1; i <= c; i++) p *= (6 - i) / 6; return p; }
    function down(x) { return Math.floor(x * 100) / 100; }
    for (var r = 1; r <= rounds; r++) {
      out.push({ round: r, bullets: r, fire_pct: Math.round(1000 * r / 6) / 10, survive: down(keep * alive(r - 1) / alive(r)),
        ride: down(keep / alive(r)), bang: down(keep * 6 / r) });
    }
    return out;
  }
  // trivia_plan() of games.py ("mixed" cycles instead of drawing at random)
  function triviaPlan(n, mode) {
    var D3 = ['easy', 'medium', 'hard'], out = [], i;
    if (D3.indexOf(mode) >= 0) { for (i = 0; i < n; i++) out.push(mode); return out; }
    if (mode === 'mixed') { for (i = 0; i < n; i++) out.push(D3[i % 3]); return out; }
    var e = Math.floor((n + 2) / 3), m = Math.floor((n + 1) / 3);
    for (i = 0; i < n; i++) out.push(i < e ? 'easy' : i < e + m ? 'medium' : 'hard');
    return out;
  }
  function sum(list, k) { return list.reduce(function (a, p) { return a + (+p[k] || 0); }, 0); }
  // A whole STATE around a game (what the server's state_view() sends).
  function roundState(game, idle) {
    return { state: game ? game.phase : 'idle', visible: true, spin: null, last: null, history: [], busy_ms: 0,
      announce: null, table: null, game: game, idle: idle || {}, preview: null };
  }
  // Characters (code points), like Python's len() in games.py: a plain emoji is ONE; a flag,
  // a skin-tone / keycap emoji or a joined (ZWJ) one is several. A cut never leaves a lone
  // UTF-16 surrogate behind, nor part of what shows as one character (half a flag, a family
  // emoji without its last members, an accent without its letter): a character that doesn't
  // fit whole is dropped - the same as games.py's _cut(), so both keep the same text.
  function chars(s) { return Array.from(String(s == null ? '' : s)); }
  var RI = /^[\u{1F1E6}-\u{1F1FF}]$/u;                                  // regional indicators: 2 = a flag
  var JOINER = /^(?:\p{M}|‍|[\u{1F3FB}-\u{1F3FF}]|[\u{E0020}-\u{E007F}])$/u;
  // does `ch` continue the character `prev` is part of? (a mark / variation selector, the
  // ZWJ, a skin tone, an emoji tag - or anything right after a ZWJ)
  function joins(prev, ch) { return prev === '‍' || JOINER.test(ch); }
  function cut(s, max) {
    var a = chars(s), i = Math.max(0, max), j;
    if (a.length <= i) return a.join('');
    while (i > 0 && joins(a[i - 1], a[i])) i--;
    for (j = i; j > 0 && RI.test(a[j - 1]); j--) {}                     // flags pair up from a run's start
    if ((i - j) % 2 && RI.test(a[i])) i--;
    return a.slice(0, i).join('');
  }
  // A config text (title, lore label) the way games.py keeps it: control characters and
  // lone surrogates out, trimmed, at most `max` characters; empty = the default.
  function cleanText(v, max, d) {
    // (with the u flag a surrogate PAIR is one code point, so only lone surrogates match)
    v = cut(String(v == null ? '' : v).replace(/[\x00-\x1f\x7f\ud800-\udfff]/gu, '').trim(), max).trim();
    return v || d;
  }

  // ---- russian roulette: the sample game (pull 1, bets open) and the demo pull ----
  var RR_OUTCOMES = { bang: 'BANG! The dummy is down', survived: 'The dummy survived every pull',
    walked: 'Everyone walked away - the dummy lives' };                // games.py _RR_OUTCOMES
  function rrSample(c, gid, ms) {
    var rounds = clamp(Math.round(num(c.rounds, 3)), 1, 5), odds = rrOdds(rounds, c.house_edge_pct), od = odds[0];
    var ps = [['alice', 200, 0], ['bob', 0, 50], ['carol', 120, 0], ['dave', 80, 20], ['erin', 0, 25], ['frank', 40, 0]].map(function (p) {
      return { user: p[0], stake: p[1], fresh: p[1], value: p[1], bang: p[2], bang_pays: Math.floor(p[2] * od.bang),
        if_survives: p[1] ? Math.floor(p[1] * od.survive) : null };
    }).sort(function (a, b) { return (b.value + b.bang) - (a.value + a.bang); });
    var cur = String(c.currency || 'coins'), dummy = { name: String(c.dummy_name || 'Dummy'), user: null };
    return roundState({
      id: gid, test: false, phase: 'betting', ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0,
      round: 1, rounds: rounds, survived: 0, bullets: 1, loaded: [], pull: null, dummy: dummy,
      edge_pct: num(c.house_edge_pct, 5), cut_pct: num(c.volunteer_cut_pct, 5), odds: odds, players: ps,
      at_risk: sum(ps, 'value'), bang_total: sum(ps, 'bang'), last: null, outcome: null, summary: null,
      currency: cur, min_bet: c.min_bet, max_bet: c.max_bet, commands_text: c.commands_text || ''
    }, { dummy: dummy, rounds: rounds, odds: odds, currency: cur });
  }
  // ▶ Preview's timings (merged into the renderer's config while it plays): the pull
  // animation runs on pull_seconds (6 s = the shortest the server allows, and its full
  // load -> spin -> cock -> pull) and the game-over card counts from result_seconds.
  function rrDemoTiming(c) {
    return { pull_seconds: 6, result_seconds: Math.min(clamp(num(c.result_seconds, 4), 2, 30), 3) };
  }
  // [{ms, state}]: the last seconds of the bet window -> the pull (bang or click, 50/50
  // here, not the real odds) -> its result -> the game-over card. With rrDemoTiming():
  // 3 + 6 + 3 + 4 = 16 s.
  function rrPreview(c, gid) {
    var s0 = rrSample(c, gid, 3000), g0 = s0.game, od = g0.odds[0];
    var fired = Math.random() < 0.5, nw = Math.floor(Math.random() * 6);
    var pull = { round: 1, new: nw, loaded: [nw], stop: fired ? nw : (nw + 1 + Math.floor(Math.random() * 5)) % 6,
      fired: fired, seed: Math.floor(Math.random() * 2147483647) };
    var winners = [], losers = [], riding = [], paid = {};
    g0.players.forEach(function (p) {
      if (fired) {
        if (p.bang) { winners.push({ user: p.user, side: 'bang', amount: p.bang_pays }); paid[p.user] = p.bang_pays; }
        if (p.value) losers.push({ user: p.user, side: 'survive', amount: p.value });
      } else {
        if (p.bang) losers.push({ user: p.user, side: 'bang', amount: p.bang });
        if (p.value) {
          var v = Math.floor(p.value * od.survive);
          winners.push({ user: p.user, side: 'survive', amount: v }); paid[p.user] = v;
          riding.push(Object.assign({}, p, { value: v, fresh: 0, bang: 0, bang_pays: 0, if_survives: null }));
        }
      }
    });
    function byAmount(a, b) { return b.amount - a.amount; }
    winners.sort(byAmount); losers.sort(byAmount);
    var last = { round: 1, fired: fired, winners: winners, losers: losers };
    var outcome = fired ? 'bang' : (g0.rounds === 1 ? 'survived' : 'walked');
    var bet = sum(g0.players, 'value') + sum(g0.players, 'bang'), out = sum(winners, 'amount');
    var summary = { outcome: outcome, text: RR_OUTCOMES[outcome], dummy: g0.dummy, rounds: g0.rounds, pulls: 1, fired_on: fired ? 1 : null,
      total_bet: bet, total_paid: out, house_net: bet - out, cut: null, test: false, currency: g0.currency,
      players: g0.players.map(function (p) {
        var b = p.value + p.bang, pd = paid[p.user] || 0;
        return { user: p.user, bet: b, paid: pd, net: pd - b };
      }).sort(function (a, b) { return b.net - a.net; }) };
    var after = { pull: pull, loaded: [nw], last: last, survived: fired ? 0 : 1, players: fired ? [] : riding,
      at_risk: fired ? 0 : sum(riding, 'value'), bang_total: 0 };
    function step(phase, secs, extra) {
      var ms = Math.round(secs * 1000), g = Object.assign({}, g0, { phase: phase, ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0 }, extra);
      return { ms: ms, state: roundState(g, s0.idle) };
    }
    return [
      step('betting', 3),
      step('pulling', clamp(num(c.pull_seconds, 9), 6, 20), { pull: pull, loaded: [nw] }),
      step('result', clamp(num(c.result_seconds, 4), 2, 30), after),
      step('over', 4, Object.assign({}, after, { players: [], at_risk: 0, outcome: outcome, summary: summary }))   // the revive: its last 3.2 s
    ];
  }

  // ---- trivia: the sample game (question 1, bets open) and the demo question ----
  var TV_Q = { category: 'Stream history', text: 'How long was this channel\'s longest stream?',
    options: ['6 hours', '12 hours', '24 hours', '48 hours'], answer: 2 };
  function tvSample(c, gid, ms) {
    var total = clamp(Math.round(num(c.questions, 15)), 1, 50), plan = triviaPlan(total, c.difficulty), d = plan[0];
    var pays = { easy: num(c.pay_easy, 1.5), medium: num(c.pay_medium, 2), hard: num(c.pay_hard, 3),
      bonus_pct: num(c.streak_bonus_pct, 10), max_multiplier: num(c.max_multiplier, 50) };
    var ps = [['alice', 200], ['bob', 150], ['carol', 120], ['dave', 80], ['erin', 50], ['frank', 25]].map(function (p) {
      return { user: p[0], status: 'in', balance: p[1], basis: p[1], streak: 0, fresh: true, if_right: Math.floor(p[1] * pays[d]), answered: false };
    });
    var cur = String(c.currency || 'coins');
    return roundState({
      id: gid, test: false, phase: 'betting', ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0,
      number: 1, total: total, plan: plan, next: { number: 1, category: TV_Q.category, difficulty: d, source: 'lore' },
      question: null, votes: null, voters: 0, players: ps, on_the_line: sum(ps, 'balance'), waiting: 0, pays: pays,
      results: [], last: null, outcome: null, summary: null, opentdb: false, currency: cur,
      min_bet: c.min_bet, max_bet: c.max_bet, commands_text: c.commands_text || ''
    }, { questions: total, difficulty: c.difficulty || 'ramp', lore: c.lore || 'mixed', pays: pays, currency: cur });
  }
  // [{ms, state}]: the last seconds of the bet window -> the question -> the votes (if
  // show_votes is before_reveal) -> the reveal -> the game-over card: at most
  // 3 + 5 + 2 + 3 + 4 = 17 s.
  function tvPreview(c, gid) {
    var s0 = tvSample(c, gid, 3000), g0 = s0.game, d = g0.plan[0], mult = g0.pays[d];
    var q = { number: 1, category: TV_Q.category, difficulty: d, source: 'lore', text: TV_Q.text, options: TV_Q.options.slice(), answer: null };
    var qa = Object.assign({}, q, { answer: TV_Q.answer });
    var picks = { alice: 2, bob: 1, carol: 2, dave: 0, erin: 3, frank: 2 }, votes = [2, 4, 7, 1];
    var asking = g0.players.map(function (p) { return Object.assign({}, p, { fresh: false, answered: p.user !== 'erin' && p.user !== 'frank' }); });
    var right = [], wrong = [];
    g0.players.forEach(function (p) {
      if (picks[p.user] === TV_Q.answer) right.push({ user: p.user, was: p.balance, balance: Math.floor(p.balance * mult), streak: 1 });
      else wrong.push({ user: p.user, lost: p.balance, answer: 'ABCDE'.charAt(picks[p.user]) });
    });
    right.sort(function (a, b) { return b.balance - a.balance; });
    wrong.sort(function (a, b) { return b.lost - a.lost; });
    var last = { number: 1, difficulty: d, source: 'lore', correct: 'ABCDE'.charAt(TV_Q.answer), votes: votes, voters: 14,
      voters_right: 7, right: right, wrong: wrong };
    var won = right.map(function (r) {
      return { user: r.user, status: 'won', balance: r.balance, basis: r.was, streak: 1, fresh: false, if_right: null, answered: false };
    });
    var results = [{ number: 1, difficulty: d, correct: last.correct }];
    var bet = sum(g0.players, 'balance'), paid = sum(right, 'balance');
    var summary = { outcome: 'complete', text: 'Every question played', questions: g0.total, total: g0.total, total_bet: bet, total_paid: paid,
      house_net: bet - paid, right: right.length, wrong: wrong.length, test: false, currency: g0.currency,
      players: g0.players.map(function (p) {
        var r = right.filter(function (x) { return x.user === p.user; })[0], pd = r ? r.balance : 0;
        return { user: p.user, bet: p.balance, paid: pd, net: pd - p.balance };
      }).sort(function (a, b) { return b.net - a.net; }) };
    function step(phase, secs, extra) {
      var ms = Math.round(secs * 1000), g = Object.assign({}, g0, { phase: phase, ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0 }, extra);
      return { ms: ms, state: roundState(g, s0.idle) };
    }
    var sv = c.show_votes || 'before_reveal', steps = [step('betting', 3)];
    steps.push(step('question', clamp(num(c.answer_seconds, 15), 3, 5), { next: null, question: q, votes: sv === 'live' ? [1, 3, 4, 1] : null,
      voters: 9, players: asking }));
    if (sv === 'before_reveal') {
      steps.push(step('votes', clamp(num(c.votes_seconds, 3), 1, 2), { next: null, question: q, votes: votes, voters: 14,
        players: asking.map(function (p) { return Object.assign({}, p, { answered: false }); }) }));
    }
    steps.push(step('reveal', clamp(num(c.reveal_seconds, 5), 2, 3), { next: null, question: qa, votes: votes, voters: 14, players: won,
      on_the_line: 0, waiting: sum(won, 'balance'), results: results, last: last }));
    steps.push(step('over', 4, { next: null, question: qa, votes: votes, voters: 14, players: [],
      on_the_line: 0, waiting: 0, results: results, last: last, outcome: 'complete', summary: summary }));
    return steps;
  }

  function selectRow(id, label, opts) {
    return '<div class="editor-row"><label>' + label + '</label><select id="' + id + '">' +
      opts.map(function (o) { return '<option value="' + o[0] + '">' + esc(o[1]) + '</option>'; }).join('') + '</select></div>';
  }

  T.openEditor = function () {
    if (this.ed) return;
    var self = this, k = this.key, rr = k === 'russian', keys = APPEARANCE[k], D = LOOK[k], R = this.R();
    var P = 'rp-' + k + '-ed-';
    var texts = keys.filter(function (t) { return TEXT_MAX[t]; });
    var w = pick(this.cfgFull(), keys);
    w.x = clamp(num(w.x, 50), 0, 100);
    w.y = clamp(num(w.y, 50), 0, 100);
    w.scale = clamp(num(w.scale, 1), 0.2, 5);
    w.players_max = clamp(Math.round(num(w.players_max, 8)), 1, 20);
    w.sfx_volume = clamp(num(w.sfx_volume, D.sfx_volume), 0, 1);
    texts.forEach(function (t) { w[t] = cleanText(w[t], TEXT_MAX[t], D[t]); });

    var TEXT_ROWS = {
      title: ['Title', rr ? 'Your branding: the name in the header' : 'Your branding: the board\'s name (header + the card between games)'],
      lore_label: ['Lore', 'What the overlay calls your own questions (the badge on a lore question)']
    };
    // (no maxlength: it counts UTF-16 units - an emoji is 2 - while games.py counts characters;
    // the text is cut to TEXT_MAX characters here instead, never mid-emoji)
    function textRow(t) {
      return '<div class="editor-row"><label>' + TEXT_ROWS[t][0] + '</label>' +
        '<input type="text" id="' + P + t + '" placeholder="' + esc(D[t]) + '" title="' + esc(TEXT_ROWS[t][1]) + '" autocomplete="off" spellcheck="false">' +
        '<div class="value" id="' + P + t + '-n"></div></div>';
    }
    var ov = document.createElement('div');
    ov.className = 'modal-overlay';
    ov.innerHTML =
      '<div class="modal">' +
        '<div class="modal-header"><span class="title">' + esc(this.title) + ' — placement &amp; look</span><button class="close" title="Close">×</button></div>' +
        '<div class="canvas-preview" id="' + P + 'preview"><div class="grid-overlay"></div>' +
          '<div class="gstage"><div class="gwheel rp-box" id="' + P + 'box"></div></div>' +
          '<div class="pmsg" id="' + P + 'msg" style="display:none"></div></div>' +
        '<div class="note" style="margin-top:-4px">Sample players in a sample game — ▶ Preview plays a local demo ' + (rr ? 'pull' : 'question') + '; nothing is bet, shown on stream or saved.</div>' +
        '<div class="editor-controls">' +
          '<div class="editor-row"><label>Scale</label><input type="range" id="' + P + 'scale" min="0.2" max="5" step="0.05" value="' + w.scale + '">' +
            '<div class="value" id="' + P + 'scaleval">' + w.scale.toFixed(2) + 'x</div></div>' +
          '<div class="editor-row"><label>Position</label>' +
            '<div class="value" style="flex:1;text-align:left;">x:<span id="' + P + 'x">' + w.x.toFixed(0) + '</span>% y:<span id="' + P + 'y">' + w.y.toFixed(0) + '</span>%</div>' +
            '<div class="quick-positions">' +
              '<button data-pos="15,15" title="Top Left">↖</button><button data-pos="50,15" title="Top">↑</button><button data-pos="85,15" title="Top Right">↗</button>' +
              '<button data-pos="15,50" title="Left">←</button><button data-pos="50,50" title="Center">●</button><button data-pos="85,50" title="Right">→</button>' +
              '<button data-pos="15,85" title="Bottom Left">↙</button><button data-pos="50,85" title="Bottom">↓</button><button data-pos="85,85" title="Bottom Right">↘</button>' +
            '</div></div>' +
          selectRow(P + 'theme', 'Theme', THEMES[k]) +
          texts.map(textRow).join('') +
          '<div class="editor-row wrap"><label>Rules</label>' +
            '<label class="tog" style="min-width:122px"><input type="checkbox" id="' + P + 'show_rules"> rules box (bets open)</label>' +
            (rr ? '<label class="tog"><input type="checkbox" id="' + P + 'show_odds"> payout ladder</label>' : '') + '</div>' +
          '<div class="editor-row wrap"><label>Players</label>' +
            '<label class="tog" style="min-width:122px"><input type="checkbox" id="' + P + 'show_players"> players board</label>' +
            '<input type="number" id="' + P + 'players_max" min="1" max="20" step="1" style="flex:0 0 80px">' +
            '<span class="value" style="min-width:0;text-align:left">players</span></div>' +
          '<div class="editor-row"><label>Sound</label>' +
            '<label class="tog" style="min-width:122px"><input type="checkbox" id="' + P + 'sfx"> sound effects</label>' +
            '<input type="range" id="' + P + 'sfx_volume" min="0" max="1" step="0.05">' +
            '<div class="value" id="' + P + 'volval">50%</div></div>' +
        '</div>' +
        '<div class="modal-actions">' +
          '<div class="left"><button class="reset">Reset</button><button class="preview">▶ Preview</button><button class="test">Test in OBS</button></div>' +
          '<div class="right"><button class="cancel">Cancel</button><button class="save">Save</button></div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(ov);

    var q = function (sel) { return ov.querySelector(sel); };
    var preview = q('#' + P + 'preview');
    var view = makeView(preview, q('.gstage'), q('#' + P + 'box'), this.baseW(), this.baseH());
    var slider = q('#' + P + 'scale');
    // demo = the ▶ Preview's shortened timings (russian: the pull animation runs on the
    // config's pull_seconds / result_seconds) while it plays, else null
    var demo = null;
    function full() { return Object.assign({}, self.cfgFull(), w, demo); }
    var sample = rr ? rrSample : tvSample, script = rr ? rrPreview : tvPreview;
    var sampleId = k + '-sample-' + Date.now().toString(36);

    // Real renderer, sound off, fed the sample game. Its bet window is fed again every 250 ms
    // with the same 30 s left, so the countdown stands still at 30 (no warnings, no jumps).
    if (R) {
      try { view.inst = R.create(view.el, full(), { sound: false }) || null; }
      catch (e) { console.error('[games] ' + k + ' renderer create failed:', e); view.inst = null; }
    }
    var sampleSt = null;
    function refeedSample() {
      if (view.inst && sampleSt) safe(function () { view.inst.setState(sampleSt); });
    }
    function feedSample() {
      if (!view.inst) return;
      sampleSt = sample(full(), sampleId, 30000);
      refeedSample();
    }
    if (!view.inst) {
      view.el.classList.add('empty');
      viewMsg(q('#' + P + 'msg'), this.title + ' renderer not loaded — placement still works.');
    } else feedSample();

    function update() {
      placeView(view, w.x, w.y, w.scale);
      q('#' + P + 'x').textContent = w.x.toFixed(0);
      q('#' + P + 'y').textContent = w.y.toFixed(0);
      q('#' + P + 'scaleval').textContent = w.scale.toFixed(2) + 'x';
    }
    update();

    var _cfgPending = false;
    function pushCfg() {
      if (_cfgPending || !view.inst) return;
      _cfgPending = true;
      requestAnimationFrame(function () {
        _cfgPending = false;
        if (!view.inst) return;
        safe(function () { view.inst.setConfig(full()); });   // (the look: the box's size is placeView's)
      });
    }

    // Drag-to-position (pointer capture), like the soundboard / roulette / craps - but also
    // while ▶ Preview plays: a round renderer just draws the STATE it is given, so moving it
    // doesn't disturb the demo.
    var previewActive = false;
    var dragging = false, startX, startY, startMx, startMy;
    view.el.addEventListener('pointerdown', function (e) {
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
    var theme = q('#' + P + 'theme'), cbs = ['show_rules', 'show_players', 'sfx'].concat(rr ? ['show_odds'] : []);
    var pmax = q('#' + P + 'players_max'), vol = q('#' + P + 'sfx_volume');
    function paintVol() { q('#' + P + 'volval').textContent = Math.round(w.sfx_volume * 100) + '%'; }
    // characters typed / allowed (amber when over: the text is cut to the allowed length)
    function paintText(t) {
      var n = chars(q('#' + P + t).value.trim()).length, el = q('#' + P + t + '-n');
      el.textContent = n + '/' + TEXT_MAX[t];
      el.style.color = n > TEXT_MAX[t] ? 'var(--amber-soft)' : '';
    }
    function syncControls() {
      slider.value = w.scale;
      theme.value = w.theme;
      if (theme.value !== w.theme) { theme.value = D.theme; w.theme = D.theme; }
      texts.forEach(function (t) { q('#' + P + t).value = w[t]; paintText(t); });
      cbs.forEach(function (c) { q('#' + P + c).checked = !!w[c]; });
      pmax.value = w.players_max;
      vol.value = w.sfx_volume;
      paintVol();
    }
    syncControls();
    theme.onchange = function () { w.theme = theme.value; pushCfg(); };
    texts.forEach(function (t) {
      var el = q('#' + P + t);
      el.oninput = function () { w[t] = cut(el.value, TEXT_MAX[t]); paintText(t); pushCfg(); };   // empty = the default, live
      el.onchange = function () { w[t] = cleanText(el.value, TEXT_MAX[t], D[t]); el.value = w[t]; paintText(t); pushCfg(); };
    });
    cbs.forEach(function (c) { q('#' + P + c).onchange = function () { w[c] = this.checked; pushCfg(); }; });
    pmax.oninput = function () { var v = parseInt(pmax.value, 10); if (!isFinite(v)) return; w.players_max = clamp(v, 1, 20); pushCfg(); };
    pmax.onchange = function () { pmax.value = w.players_max; };
    vol.oninput = function () { w.sfx_volume = clamp(num(vol.value, D.sfx_volume), 0, 1); paintVol(); pushCfg(); };

    // ---- ▶ Preview: a local demo round through setState() (no server call) ----
    var previewBtn = q('.modal-actions .preview');
    var previewTok = 0, previewTimer = null;
    function endDemo() {
      demo = null;
      if (view.inst) safe(function () { view.inst.setConfig(full()); });   // the saved timings again
    }
    function previewDone(tok) {
      if (tok !== previewTok) return;
      clearTimeout(previewTimer);
      previewActive = false;
      previewBtn.classList.remove('playing');
      previewBtn.textContent = '▶ Preview';
      endDemo();
      feedSample();                       // the game is over: back to the sample bet window
    }
    function stopPreview() {
      if (!previewActive) return;
      previewTok++;
      clearTimeout(previewTimer);
      previewActive = false;
      previewBtn.classList.remove('playing');
      previewBtn.textContent = '▶ Preview';
      endDemo();
      if (view.inst) { safe(function () { view.inst.reset(); }); feedSample(); }
    }
    if (!view.inst) { previewBtn.disabled = true; previewBtn.title = k + ' renderer not loaded'; }
    previewBtn.onclick = function () {
      if (!view.inst) return;
      if (previewActive) { stopPreview(); return; }
      var steps = null;
      demo = rr ? rrDemoTiming(full()) : null;
      try { steps = script(full(), k + '-preview-' + Date.now().toString(36)); }
      catch (e) { console.error('[games] ' + k + ' preview failed:', e); }
      if (!steps || !steps.length) { demo = null; toast('Preview failed — see the browser console', 2400); return; }
      previewActive = true;
      previewBtn.classList.add('playing');
      previewBtn.textContent = '⏸ Stop';
      var tok = ++previewTok, i = 0;
      safe(function () { view.inst.reset(); });
      if (demo) safe(function () { view.inst.setConfig(full()); });          // the demo's timings
      (function next() {
        if (tok !== previewTok) return;
        if (i >= steps.length) { previewDone(tok); return; }
        var s = steps[i++];
        safe(function () { view.inst.setState(s.state); });
        previewTimer = setTimeout(next, s.ms);
      })();
    };
    // the sample's bet window: 30 s left, again and again (a steady countdown)
    var sampleTimer = setInterval(function () { if (!previewActive) refeedSample(); }, 250);

    // ---- actions ----
    function snapshot() {
      var o = pick(w, keys);
      o.x = Math.round(w.x * 100) / 100;
      o.y = Math.round(w.y * 100) / 100;
      o.scale = Math.round(w.scale * 100) / 100;
      o.sfx_volume = Math.round(w.sfx_volume * 100) / 100;
      texts.forEach(function (t) { o[t] = cleanText(w[t], TEXT_MAX[t], D[t]); });
      return o;
    }
    function onKey(e) { if (e.key === 'Escape') { e.preventDefault(); close(); } }
    function onWinResize() { fitView(view); }
    if (!view.ro) window.addEventListener('resize', onWinResize);
    function close() {
      stopPreview();
      clearInterval(sampleTimer);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onWinResize);
      if (view.ro) view.ro.disconnect();
      if (view.inst) { var inst = view.inst; view.inst = null; safe(function () { inst.destroy(); }); }
      ov.remove();
      self.ed = null;
    }
    document.addEventListener('keydown', onKey);
    q('.modal-header .close').onclick = close;
    q('.modal-actions .cancel').onclick = close;
    closeOnBackdrop(ov, close);

    // Test in OBS: the game on the real overlay for a few seconds with this look (/preview)
    q('.modal-actions .test').onclick = async function () {
      var d = await api('POST', self.api + '/preview', { overrides: snapshot() });
      if (d) toast('fired to OBS — not saved yet');
    };
    q('.modal-actions .save').onclick = async function () {
      var body = {}; body[k] = snapshot();
      var d = await api('POST', '/games/api/config', body);
      if (!d) return;
      if (isObj(d.config) && isObj(d.config[k])) self.onConfig(d.config);
      else { self.cfg = Object.assign({}, self.cfg, snapshot()); self.applyMirror(); }
      toast('saved ' + self.title.toLowerCase() + ' placement');
      // Modal stays open so you can keep fine-tuning. Close via × / Cancel / Esc / click-outside.
    };
    q('.modal-actions .reset').onclick = function () {
      Object.assign(w, pick(D, keys));
      syncControls(); update(); pushCfg();
      toast('defaults restored — not saved yet');
    };

    // saved config changed elsewhere (e.g. Settings): re-render with our working look
    this.ed = { refresh: function () { pushCfg(); if (!previewActive) feedSample(); }, close: close };
  };

  // ---------------------------------------------------------------- readout
  T.tick = function () { this.renderState(); };
  // A trivia question's category as the overlay names it: a lore question without one
  // (category "") goes by the lore label - the one on screen, so a Test in OBS' too.
  T.catName = function (h) {
    return h.category || (h.source === 'lore' ? String(this.mirrorEffective().lore_label || LOOK.trivia.lore_label) : '?');
  };
  T.renderState = function () {
    var st = this.st, g = st && st.game, $$ = this.$.bind(this);
    if (!st) { $$('state').textContent = '—'; return; }
    var ph = g ? g.phase : 'idle', dt = performance.now() - this.stAt;
    var left = g && g.ends_in_ms != null ? Math.max(0, g.ends_in_ms - dt) : null;
    $$('state').textContent = (PHASES[this.key][ph] || ph) + (left != null ? ' · ' + Math.ceil(left / 1000) + 's' : '');
    var sub = '';
    if (g) {
      if (this.key === 'russian') sub = 'pull ' + g.round + ' of ' + g.rounds + ' · ' + g.round + ' bullet' + (g.round > 1 ? 's' : '') + (g.test ? ' · TEST GAME' : '');
      else sub = 'question ' + g.number + ' of ' + g.total + (g.next ? ' · ' + this.catName(g.next) + ' (' + g.next.difficulty + ')' : '') + (g.test ? ' · TEST GAME' : '');
    } else sub = 'no game running';
    $$('sub').textContent = sub;
  };
  T.render = function () {
    this.renderState();
    var st = this.st, g = st && st.game, rr = this.key === 'russian', cur = (g && g.currency) || this.cfg.currency || 'coins';
    var facts = [];
    if (g) {
      if (rr) {
        facts = [['Dummy', esc(g.dummy.name) + (g.dummy.user ? ' <span class="note">(@' + esc(g.dummy.user) + ')</span>' : '')],
          ['Survived', g.survived + ' of ' + g.rounds], ['Survive stakes', fmt(g.at_risk) + ' ' + esc(cur)], ['Bang bets', fmt(g.bang_total) + ' ' + esc(cur)]];
        if (g.pull && (g.phase === 'result' || g.phase === 'over')) facts.push(['Last pull', g.pull.fired ? '💥 BANG' : 'click']);
      } else {
        var q = g.question;
        facts = [['On the line', fmt(g.on_the_line) + ' ' + esc(cur)], ['Waiting (won)', fmt(g.waiting) + ' ' + esc(cur)],
          ['Answers in', fmt(g.voters)], ['Question', q ? esc(cut(q.text, 80)) : (g.next ? esc(this.catName(g.next)) : '—')]];
        if (q && q.answer != null) facts.push(['Answer', esc('ABCDE'[q.answer] + ': ' + q.options[q.answer])]);
      }
      if (g.summary) facts.push(['Result', esc(g.summary.text) + ' · house ' + signed(g.summary.house_net)]);
    } else if (st && st.idle) {
      facts = rr ? [['Next dummy', esc((st.idle.dummy || {}).name || '')], ['Pulls', st.idle.rounds]] :
        [['Next game', st.idle.questions + ' questions'], ['Difficulty', esc(st.idle.difficulty)], ['Lore', esc(st.idle.lore)]];
    }
    this.$('facts').innerHTML = facts.map(function (f) { return '<div><span class="lbl">' + f[0] + '</span><b>' + f[1] + '</b></div>'; }).join('');
    // players
    var ps = (g && g.players) || [], rows;
    this.$('ppill').textContent = ps.length ? ps.length + ' playing' : 'nobody';
    if (rr) rows = '<tr><th>User</th><th class="num">Survive stake</th><th class="num">Worth now</th><th class="num">If it clicks</th><th class="num">Bang</th></tr>' +
      ps.map(function (p) { return '<tr><td>' + esc(p.user) + '</td><td class="num">' + fmt(p.stake) + '</td><td class="num">' + fmt(p.value) + '</td><td class="num">' + (p.if_survives != null ? fmt(p.if_survives) : '—') + '</td><td class="num">' + (p.bang ? fmt(p.bang) + ' → ' + fmt(p.bang_pays) : '—') + '</td></tr>'; }).join('');
    else rows = '<tr><th>User</th><th>Status</th><th class="num">Balance</th><th class="num">Put in</th><th class="num">Streak</th><th class="num">If right</th></tr>' +
      ps.map(function (p) { return '<tr><td>' + esc(p.user) + '</td><td>' + (p.status === 'won' ? 'won · ride or cash out' : (p.fresh ? 'bet' : 'riding')) + (p.answered ? ' ✔' : '') + '</td><td class="num">' + fmt(p.balance) + '</td><td class="num">' + fmt(p.basis) + '</td><td class="num">' + p.streak + '</td><td class="num">' + (p.if_right != null ? fmt(p.if_right) : '—') + '</td></tr>'; }).join('');
    this.$('players').innerHTML = ps.length ? rows : '<tr><td class="note">Nobody has coins in this game.</td></tr>';
  };

  // ---------------------------------------------------------------- history
  T.loadHistory = async function () {
    var r = await this.req('GET', this.api + '/history?limit=20');
    if (r.ok) { this.stats = r.d; this.renderHistory(); }
  };
  T.renderHistory = function () {
    var d = this.stats || {}, s = d.stats || {}, rr = this.key === 'russian';
    var bits = rr ? [['Games', s.games], ['Bangs', s.bangs], ['Survived', s.survived], ['Walked', s.walked], ['Total bet', fmt(s.total_bet)], ['Paid', fmt(s.total_paid)], ['House net', signed(s.house_net)]]
      : [['Games', s.games], ['Complete', s.complete], ['Questions', s.questions], ['Right', s.right], ['Wrong', s.wrong], ['Total bet', fmt(s.total_bet)], ['Paid', fmt(s.total_paid)], ['House net', signed(s.house_net)]];
    this.$('stats').innerHTML = bits.map(function (b) { return '<span>' + b[0] + ' <b>' + (b[1] == null ? 0 : b[1]) + '</b></span>'; }).join('');
    var h = d.history || [];
    this.$('hist').innerHTML = h.length ? '<tr><th>Ended</th><th>Result</th><th class="num">Bet</th><th class="num">Paid</th><th class="num">House</th></tr>' +
      h.map(function (e) {
        var r = e.result || {};
        return '<tr><td>' + fmtTime(e.ended_at) + (e.test ? ' <span class="note">test</span>' : '') + '</td><td>' + esc(r.text || r.outcome) +
          (r.dummy ? ' <span class="note">(' + esc(r.dummy.name) + ')</span>' : '') + (r.questions != null ? ' <span class="note">' + r.questions + '/' + r.total + '</span>' : '') +
          '</td><td class="num">' + fmt(r.total_bet) + '</td><td class="num">' + fmt(r.total_paid) + '</td><td class="num">' + signed(r.house_net) + '</td></tr>';
      }).join('') : '<tr><td class="note">No games yet.</td></tr>';
  };

  // ---------------------------------------------------------------- ledger
  T.fetchLedger = async function (since) {
    var r = await this.req('GET', this.api + '/ledger?since=' + (since || 0) + '&limit=5000');
    if (r.ok) { this.addEvents(r.d.events || []); this.renderLedger(); }
  };
  T.addEvents = function (evs) {
    for (var i = 0; i < evs.length; i++) {
      var e = evs[i];
      if (!e || e.seq == null || this.lseen[e.seq]) continue;
      this.lseen[e.seq] = 1; this.ledger.push(e);
      if (e.seq > this.lastSeq) this.lastSeq = e.seq;
    }
    this.ledger.sort(function (a, b) { return a.seq - b.seq; });
    if (this.ledger.length > 3000) this.ledger = this.ledger.slice(-3000);
  };
  T.renderLedger = function () {
    var rows = this.ledger.slice(-200).reverse();
    this.$('ledger').innerHTML = rows.length ? '<tr><th class="num">Seq</th><th>Time</th><th>User</th><th>Type</th><th class="num">Amount</th><th>Reason</th><th>Bet</th></tr>' +
      rows.map(function (e) {
        return '<tr><td class="num">' + e.seq + '</td><td class="dim">' + fmtTime(e.ts) + '</td><td>' + esc(e.user) + '</td><td>' + esc(e.type) +
          '</td><td class="num">' + (e.type === 'debit' ? '−' : '+') + fmt(e.amount) + '</td><td class="dim">' + esc(REASONS[e.reason] || e.reason) +
          '</td><td class="dim">' + esc(e.bet || '') + '</td></tr>';
      }).join('') : '<tr><td class="dim">No coin movements yet.</td></tr>';
  };

  // ---------------------------------------------------------------- trivia: lore + bank
  T.loadLore = async function () {
    var r = await this.req('GET', this.api + '/lore');
    if (!r.ok) return;
    var qs = r.d.questions || [];
    this.$('lorecount').textContent = qs.length + ' question' + (qs.length === 1 ? '' : 's');
    this.$('lorelist').innerHTML = qs.length ? '<tr><th>Question</th><th>Diff.</th><th></th></tr>' + qs.slice().reverse().map(function (q) {
      return '<tr><td><div class="q">' + esc(q.question) + '</div><div class="a">✓ ' + esc(q.correct) + '</div><div class="w">✗ ' + q.incorrect.map(esc).join(' · ') +
        '</div></td><td>' + esc(q.difficulty) + '</td><td class="act"><button class="sec" data-del="' + esc(q.id) + '">Delete</button></td></tr>';
    }).join('') : '<tr><td class="note">No lore questions yet — add the channel\'s own questions above.</td></tr>';
  };
  T.loadBank = async function () { var r = await this.req('GET', this.api + '/bank'); if (r.ok) this.renderBank(r.d); };
  T.renderBank = function (b) {
    var p = b.pool || {};
    this.$('bank').innerHTML = 'Question pool: <b>' + (p.easy || 0) + '</b> easy · <b>' + (p.medium || 0) + '</b> medium · <b>' + (p.hard || 0) +
      '</b> hard (more are fetched as needed) · lore <b>' + b.lore_unasked + '</b>/' + b.lore + ' unasked · asked recently <b>' + b.asked + '</b> ' +
      '<button class="sec" data-clear="1" style="padding:3px 9px;font-size:12px">New night</button>' +
      (b.last_error ? '<div class="err">' + esc(b.last_error) + '</div>' : '');
  };
  T.loadCategories = async function () {
    var r = await this.req('GET', this.api + '/categories');
    var sel = this.$('s-category');
    if (!sel || !r.ok) return;
    var cur = String(this.cfg.category || 0);
    sel.innerHTML = '<option value="0">Any category</option>' + (r.d.categories || []).map(function (c) {
      return '<option value="' + c.id + '">' + esc(c.name) + '</option>';
    }).join('');
    sel.value = cur;
  };
  T.loadClips = async function () {
    if (typeof GP.loadClipList === 'function') { try { GP.loadClipList(); } catch (e) {} }
  };

  // ---------------------------------------------------------------- settings
  T.buildSettings = function () {
    var self = this, html = '';
    this.dirty = {};
    SETTINGS[this.key].forEach(function (f) {
      var id = self.id('s-' + f[0]), t = f[4] ? ' title="' + esc(f[4]) + '"' : '';
      if (f[2] === 'bool') html += '<label class="f sw"' + t + '><input type="checkbox" id="' + id + '"> ' + esc(f[1]) + '</label>';
      else if (f[2] === 'select') html += '<label class="f"' + t + '>' + esc(f[1]) + '<select id="' + id + '">' + f[3].map(function (o) { return '<option value="' + o[0] + '">' + esc(o[1]) + '</option>'; }).join('') + '</select></label>';
      else if (f[2] === 'category') html += '<label class="f"' + t + '>' + esc(f[1]) + '<select id="' + id + '"><option value="0">Any category</option></select></label>';
      // (text: no maxlength - it counts UTF-16 units, an emoji is 2, while games.py counts
      // characters; limitText() below cuts to f[3] characters instead, as the editor does)
      else if (f[2] === 'text' || f[2] === 'clip') html += '<label class="f"' + t + '>' + esc(f[1]) + '<input id="' + id + '"' + (f[2] === 'clip' ? ' list="clip-list" placeholder="clip name (optional)"' : '') + ' autocomplete="off"></label>';
      else html += '<label class="f"' + t + '>' + esc(f[1]) + '<input type="number" id="' + id + '" min="' + f[3][0] + '" max="' + f[3][1] + '" step="' + (f[2] === 'int' ? 1 : 'any') + '"></label>';
    });
    this.$('settings').innerHTML = html;
    SETTINGS[this.key].forEach(function (f) {
      var el = $(self.id('s-' + f[0]));
      var mark = function () { self.dirty[f[0]] = true; self.$('dirty').textContent = 'unsaved changes'; };
      if (f[2] === 'text') {
        el.addEventListener('input', function (e) { if (!e.isComposing) limitText(el, f[3]); });
        el.addEventListener('change', function () { limitText(el, f[3]); });
      }
      el.addEventListener('input', mark); el.addEventListener('change', mark);
    });
  };
  // A Settings text field past `max` characters (code points, like games.py) is cut back to
  // `max` - never mid-emoji - keeping the caret where it was.
  function limitText(el, max) {
    if (chars(el.value).length <= max) return;
    var at = el.selectionStart, v = el.value;
    if (at == null) { el.value = cut(v, max); return; }
    // like maxlength: the overflow comes out of what was just typed (before the caret), the rest stays
    var before = v.slice(0, at), after = v.slice(at), room = max - chars(after).length;
    if (room < 0) { el.value = cut(v, max); return; }
    before = cut(before, room);
    el.value = before + after;
    try { el.setSelectionRange(before.length, before.length); } catch (e) {}
  }
  T.fillSettings = function (force) {
    var self = this, c = this.cfg;
    if (force) this.dirty = {};
    SETTINGS[this.key].forEach(function (f) {
      if (self.dirty[f[0]]) return;
      var el = $(self.id('s-' + f[0]));
      if (!el || document.activeElement === el) return;
      var v = c[f[0]];
      if (f[2] === 'bool') el.checked = !!v;
      else if (f[2] === 'category') { if (![].some.call(el.options, function (o) { return o.value === String(v); })) el.insertAdjacentHTML('beforeend', '<option value="' + (+v || 0) + '">category ' + (+v || 0) + '</option>'); el.value = String(v || 0); }
      else el.value = v == null ? '' : v;
    });
    if (force || !Object.keys(this.dirty).length) this.$('dirty').textContent = '';
  };
  T.saveSettings = async function () {
    var self = this, out = {};
    SETTINGS[this.key].forEach(function (f) {
      var el = $(self.id('s-' + f[0]));
      if (f[2] === 'bool') out[f[0]] = el.checked;
      else if (f[2] === 'int' || f[2] === 'num' || f[2] === 'category') out[f[0]] = num(el.value, self.cfg[f[0]]);
      else if (f[2] === 'text') out[f[0]] = cut(el.value.trim(), f[3]);   // what games.py keeps (characters)
      else out[f[0]] = el.value;
    });
    var body = {}; body[this.key] = out;
    var r = await this.req('POST', '/games/api/config', body);
    if (r.ok) { this.dirty = {}; this.$('dirty').textContent = ''; toast('Settings saved'); if (this.key === 'trivia') this.loadBank(); }
    else toast(r.d.error || 'save failed');
  };

  // ======================================================================
  // registration
  // ======================================================================
  window.GamePanels = window.GamePanels || {};
  window.RoundPanels = window.RoundPanels || {};     // debug / test hook: RoundPanels.trivia.openEditor()
  [['russian', 'Russian Roulette'], ['trivia', 'Trivia']].forEach(function (g) {
    if (!$('tab-' + g[0])) return;
    var tab = new Tab(g[0], g[1]);
    window.RoundPanels[g[0]] = { tab: tab, openEditor: function () { tab.openEditor(); } };
    window.GamePanels[g[0]] = {
      onConfig: function (c) { tab.onConfig(c); }, onState: function (s) { tab.onState(s); },
      onStop: function (k) { tab.onStop(k); }, onLedger: function (e) { tab.onLedger(e); },
      onLink: function (on) { tab.onLink(on); }, onTab: function (k) { tab.onTab(k); }
    };
    // catch up on what the page's socket already delivered
    var last = GP.last || {};
    if (last.config) tab.onConfig(last.config);
    if (last.states && last.states[g[0]]) tab.onState(last.states[g[0]]);
    if (last.link != null) tab.onLink(last.link);
    if (location.hash.slice(1) === g[0]) tab.onTab(g[0]);
  });
})();
