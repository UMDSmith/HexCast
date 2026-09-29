/*
 * Hexcast Games — round-game panel                              round_common.js
 *
 * Russian Roulette and Trivia are the same panel with different rules: an OBS card, the live mirror, the
 * game controls, "play as", players, history, ledger, settings, the placement & look editor and the API
 * card. This is that panel. games_panel.html loads it (once) before any game's panel script;
 * russian_panel.js / trivia_panel.js describe their game and hand it over:
 *
 *   window.GamesPage.round.register(definition)
 *
 * which builds section#tab-KEY, registers window.GamePanels[KEY] (onConfig, onState, onStop, onLedger,
 * onLink, onTab, onRemove) and catches up on what the page's socket already delivered. Each tab:
 *   1. OBS browser source      both overlay URLs + Copy
 *   2. The game                live mirror (the real renderer, sound off), state + countdown,
 *                              start / test game / skip / stop / show / hide; Edit Mode ->
 *                              click -> placement & look editor (the craps / roulette modal:
 *                              sample game, drag, quick grid, look, branding, ▶ Preview,
 *                              Test in OBS = /preview, Save)
 *   3. Play as                 test a player by hand: bet, cash out, ride, answer (REAL coins
 *                              unless the game is a test game)
 *   4. Players                 everyone with coins in the game
 *   5. (extra cards)           whatever the game adds (definition.extra)
 *   6. History & stats · 7. Ledger (this game's events) · 8. Settings · 9. API
 *
 * A definition (all of it the game's own, nothing here knows a game by name):
 *   key, title                 'russian', 'Russian Roulette'  (the tab is section#tab-KEY)
 *   phases {phase: label}      the phase names shown in the state line
 *   themes [[value, label]]    the editor's theme list            look {..}   the editor's Reset (= games.py DEFAULTS)
 *   appearance [keys]          what a /preview's overrides may carry = what the editor edits
 *   base [w, h]                the renderer's BASE_W x BASE_H (if it is not loaded)
 *   fieldMax {id: chars}       the game card's own text fields, cut to that many characters
 *   settings [[key, label, kind, extra, title]]   the Settings card (kind: int num text bool select clip category)
 *   reasons {reason: label}    ledger reasons of this game on top of the common ones
 *   textRows {key: [label, tooltip]}   the editor's text rows (keys of `appearance` that are in TEXT_MAX)
 *   hint, scene, ledgerNote, doc       card wording (scene: 'scene' / 'board'; doc: docs/<doc>.md)
 *   controls(id), play(id), extra(id)  markup of the game controls, the "play as" form, extra cards (id(name) = element id)
 *   apiRows(a), examples(h)            the API card: [[endpoint, text]] and the curl lines
 *   starting                           what the output line says while a game starts (default: nothing)
 *   startBody(body, $$, field)         fills the /start request from the controls
 *   betBody(body, $$)                  fills the /bet request from the "play as" form
 *   wire(tab, $$, who, pout, field)    the game's own listeners (Set dummy, Ride, answer letters, lore ...)
 *   onConfig(tab), onFirstTab(tab), afterSave(tab)   hooks
 *   sub(tab, g), facts(tab, g, cur), idleFacts(idle), playerRows(ps), historyBits(stats)   what the readouts show
 *   demoWord, rulesExtra(P), extraChecks [keys], sample(c, gid, ms), preview(c, gid), demoTiming(c)   the editor
 *   methods {name: fn}         extra methods of the tab (trivia's lore / bank loaders)
 *   css                        rules only this game's markup uses
 *
 * Plain browser script, loaded after panel_common.js; the page's socket reaches a tab through
 * window.GamePanels[KEY]; window.GamesPage.last lets it catch up if it loads late.
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
    '.rp-lscroll{max-height:320px;overflow:auto}',
    '.rp-stats{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--dim);margin-bottom:10px}',
    '.rp-stats b{color:var(--ink);font-size:15px}'
  ].join('\n');
  (function () { var s = document.createElement('style'); s.textContent = CSS; document.head.appendChild(s); })();

  // ======================================================================
  // shared definitions
  // ======================================================================
  var TEXT_MAX = { title: 32, lore_label: 24 };                        // games.py ROUND_TITLE_MAX / LORE_LABEL_MAX
  var REASONS = { bet: 'bet', add: 'added to bet', win: 'win', cashout: 'cash out', refund: 'refund / taken back' };

  // ======================================================================
  // one tab
  // ======================================================================
  function Tab(def) {
    this.def = def;                // the game's definition (see the header)
    this.key = def.key;
    this.title = def.title;
    this.host = $('tab-' + def.key);
    this.api = '/games/api/' + def.key;
    this.cfg = {};
    this.st = null; this.stAt = 0;
    this.mir = null;               // the live mirror's view (mir.inst = its renderer)
    this.mfailed = false;          // the renderer is missing / create() threw: a placeholder box
    this.ed = null;                // the open placement & look editor: {refresh, close}
    this.ledger = []; this.lseen = {}; this.lastSeq = 0; this.lbooted = false;
    this.stats = null;
    if (!this.host) return;
    var self = this;
    Object.keys(def.methods || {}).forEach(function (m) { self[m] = def.methods[m]; });   // the game's own methods
    this.build();
    this.timer = setInterval(function () { if (self.shown()) self.tick(); }, 250);
  }
  var T = Tab.prototype;
  T.shown = function () { return this.host.classList.contains('sel'); };
  T.R = function () { var R = window.HexGames && window.HexGames[this.key]; return R && typeof R.create === 'function' ? R : null; };
  T.baseW = function () { var R = this.R(); return (R && +R.BASE_W) || this.def.base[0]; };
  T.baseH = function () { var R = this.R(); return (R && +R.BASE_H) || this.def.base[1]; };
  // Saved config with every key filled in (the renderer's defaults, then the look's).
  T.cfgFull = function () { var R = this.R(); return Object.assign({}, R && R.DEFAULTS, this.def.look, this.cfg); };
  T.id = function (s) { return 'rp-' + this.key + '-' + s; };
  // (looked up inside the tab's own section, not the document: after the game is removed a late timer or
  // fetch reply still finds its elements instead of throwing)
  T.$ = function (s) { return this.host.querySelector('#' + this.id(s)); };

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
    var k = this.key, id = this.id.bind(this), def = this.def;
    var base = location.protocol + '//' + location.host + '/games/overlay';
    var controls = def.controls(id);
    var play = def.play(id);
    var lore = def.extra ? def.extra(id) : '';        // the game's extra cards, between Players and History

    this.host.innerHTML =
      '<div class="card"><h2>OBS browser source</h2><p class="hint">The first URL shows every game in one source; the second only ' + esc(this.title) + '. It stays transparent between games unless <b>Hide between games</b> is off (or you press Show).</p>' +
      '<div class="url"><a id="' + id('u1') + '" target="_blank" rel="noopener">' + esc(base) + '</a><button class="sec" id="' + id('c1') + '">Copy</button></div>' +
      '<div class="url"><a id="' + id('u2') + '" target="_blank" rel="noopener">' + esc(base + '?game=' + k) + '</a><button class="sec" id="' + id('c2') + '">Copy</button></div></div>' +

      '<div class="card rp-card" id="' + id('card') + '"><h2>' + esc(this.title) + ' <span class="pill" id="' + id('pill') + '">connecting…</span></h2>' +
      '<p class="hint">' + def.hint +
      ' Turn on <b>Edit Mode</b> and click this card to place and style the ' + def.scene + '.</p>' +
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
      '<div class="card"><h2>Ledger</h2><p class="hint">This game\'s coin movements (the games ledger is shared: seq gaps are other games). <b>debit</b> = the bank takes a stake, <b>credit</b> = it pays (a win, a cash-out, a refund' + def.ledgerNote + '). Test games never appear here.</p>' +
      '<div class="rp-lscroll"><table class="lg" id="' + id('ledger') + '"></table></div></div>' +
      '<div class="card"><h2>Settings <span class="note" id="' + id('dirty') + '"></span></h2>' +
      '<p class="hint">Timing, money rules, branding and sounds for ' + esc(this.title) + '. Placement and look live in <b>Edit Mode</b> (click the ' + esc(this.title) + ' card). Soundboard clips fire server-side on the soundboard overlay — keep that browser source in the scene too.</p>' +
      '<div class="grid" id="' + id('settings') + '"></div>' +
      '<div class="row"><button class="act" id="' + id('save') + '">Save settings</button><button class="sec" id="' + id('revert') + '">Revert</button></div></div>' +
      '<div class="card"><h2>API</h2><p class="hint">Hexcast never reads chat: your bot turns chat commands into these calls. Full reference: <code>docs/' + def.doc + '.md</code>.</p>' +
      '<table class="api">' + this.apiRows() + '</table><h3>Examples</h3><pre>' + esc(this.examples()) + '</pre></div>';

    this.wire();
    this.buildSettings();
  };

  T.apiRows = function () {
    var a = '/games/api/' + this.key;
    return this.def.apiRows(a).map(function (r) { return '<tr><td>' + esc(r[0]) + '</td><td>' + esc(r[1]) + '</td></tr>'; }).join('');
  };
  T.examples = function () {
    return this.def.examples('http://' + location.host + '/games/api/' + this.key);
  };

  // ---------------------------------------------------------------- wiring
  T.wire = function () {
    var self = this, $$ = this.$.bind(this), k = this.key, FIELD_MAX = this.def.fieldMax;
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
      self.def.startBody(b, $$, field);
      return b;
    }
    $$('start').onclick = function () {
      $$('out').className = 'rp-out'; $$('out').textContent = self.def.starting || '';
      self.act('/start', startBody(false), 'Game started — bets are open');
    };
    $$('test').onclick = function () { self.act('/start', startBody(true), 'Test game started — no coins move'); };
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
      if (self.def.betBody) self.def.betBody(b, $$);
      self.act('/bet', b, function (d) { return 'Bet ' + fmt(d.amount) + (d.side ? ' on ' + d.side : '') + ' (debit #' + ((d.debits || [])[0] || {}).seq + ')'; }, pout);
    };
    $$('cash').onclick = function () {
      self.act('/cashout', { user: who() }, function (d) { var c = (d.credits || [])[0]; return c ? 'Cashed out ' + fmt(c.amount) : 'Nothing to pay'; }, pout);
    };
    if (self.def.wire) self.def.wire(self, $$, who, pout, field);   // the game's own listeners (Set dummy, Ride, lore ...)
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
      this.def.appearance.forEach(function (k) { if (pv.overrides[k] != null) c[k] = pv.overrides[k]; });
    }
    return c;
  };
  T.applyMirror = function () {
    var v = this.mir, R = this.R(), c = this.mirrorEffective();
    if (!v) return;
    if (!v.inst && !this.mfailed) {
      if (!R) {
        this.mfailed = true;
        viewMsg(this.$('msg'), this.title + ' renderer not loaded (' + ((GP.gameScript && GP.gameScript(this.key)) || this.key + '.js') + ').');
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
    if (this.def.onConfig) this.def.onConfig(this);
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
    if (!this.lbooted) { this.lbooted = true; this.fetchLedger(0); this.loadHistory(); this.loadClips(); if (this.def.onFirstTab) this.def.onFirstTab(this); }
  };

  // ======================================================================
  // Edit Mode: placement & look editor (roulette's / craps' openEditor(), same modal)
  // ======================================================================
  // The round renderers have no play() / setTable(): they draw a STATE. So the editor
  // feeds its own renderer a sample game in its betting window (players, rules, odds),
  // ▶ Preview steps a local demo round through setState(), and Test in OBS is the
  // server's /preview (the look on the real overlay for a few seconds, not saved).

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

  function selectRow(id, label, opts) {
    return '<div class="editor-row"><label>' + label + '</label><select id="' + id + '">' +
      opts.map(function (o) { return '<option value="' + o[0] + '">' + esc(o[1]) + '</option>'; }).join('') + '</select></div>';
  }

  T.openEditor = function () {
    if (this.ed) return;
    var self = this, k = this.key, def = this.def, keys = def.appearance, D = def.look, R = this.R();
    var P = 'rp-' + k + '-ed-';
    var texts = keys.filter(function (t) { return TEXT_MAX[t]; });
    var w = pick(this.cfgFull(), keys);
    w.x = clamp(num(w.x, 50), 0, 100);
    w.y = clamp(num(w.y, 50), 0, 100);
    w.scale = clamp(num(w.scale, 1), 0.2, 5);
    w.players_max = clamp(Math.round(num(w.players_max, 8)), 1, 20);
    w.sfx_volume = clamp(num(w.sfx_volume, D.sfx_volume), 0, 1);
    texts.forEach(function (t) { w[t] = cleanText(w[t], TEXT_MAX[t], D[t]); });

    var TEXT_ROWS = def.textRows;
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
        '<div class="note" style="margin-top:-4px">Sample players in a sample game — ▶ Preview plays a local demo ' + def.demoWord + '; nothing is bet, shown on stream or saved.</div>' +
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
          selectRow(P + 'theme', 'Theme', def.themes) +
          texts.map(textRow).join('') +
          '<div class="editor-row wrap"><label>Rules</label>' +
            '<label class="tog" style="min-width:122px"><input type="checkbox" id="' + P + 'show_rules"> rules box (bets open)</label>' +
            (def.rulesExtra ? def.rulesExtra(P) : '') + '</div>' +
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
    var sample = def.sample, script = def.preview;
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
    var theme = q('#' + P + 'theme'), cbs = ['show_rules', 'show_players', 'sfx'].concat(def.extraChecks || []);
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
      demo = def.demoTiming ? def.demoTiming(full()) : null;
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
  T.renderState = function () {
    var st = this.st, g = st && st.game, $$ = this.$.bind(this);
    if (!st) { $$('state').textContent = '—'; return; }
    var ph = g ? g.phase : 'idle', dt = performance.now() - this.stAt;
    var left = g && g.ends_in_ms != null ? Math.max(0, g.ends_in_ms - dt) : null;
    $$('state').textContent = (this.def.phases[ph] || ph) + (left != null ? ' · ' + Math.ceil(left / 1000) + 's' : '');
    var sub = '';
    if (g) {
      sub = this.def.sub(this, g);
    } else sub = 'no game running';
    $$('sub').textContent = sub;
  };
  T.render = function () {
    this.renderState();
    var st = this.st, g = st && st.game, cur = (g && g.currency) || this.cfg.currency || 'coins';
    var facts = [];
    if (g) {
      facts = this.def.facts(this, g, cur);
      if (g.summary) facts.push(['Result', esc(g.summary.text) + ' · house ' + signed(g.summary.house_net)]);
    } else if (st && st.idle) {
      facts = this.def.idleFacts(st.idle);
    }
    this.$('facts').innerHTML = facts.map(function (f) { return '<div><span class="lbl">' + f[0] + '</span><b>' + f[1] + '</b></div>'; }).join('');
    // players
    var ps = (g && g.players) || [], rows;
    this.$('ppill').textContent = ps.length ? ps.length + ' playing' : 'nobody';
    rows = this.def.playerRows(ps);
    this.$('players').innerHTML = ps.length ? rows : '<tr><td class="note">Nobody has coins in this game.</td></tr>';
  };

  // ---------------------------------------------------------------- history
  T.loadHistory = async function () {
    var r = await this.req('GET', this.api + '/history?limit=20');
    if (r.ok) { this.stats = r.d; this.renderHistory(); }
  };
  T.renderHistory = function () {
    var d = this.stats || {}, s = d.stats || {};
    var bits = this.def.historyBits(s);
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
    var reasons = Object.assign({}, REASONS, this.def.reasons);
    var rows = this.ledger.slice(-200).reverse();
    this.$('ledger').innerHTML = rows.length ? '<tr><th class="num">Seq</th><th>Time</th><th>User</th><th>Type</th><th class="num">Amount</th><th>Reason</th><th>Bet</th></tr>' +
      rows.map(function (e) {
        return '<tr><td class="num">' + e.seq + '</td><td class="dim">' + fmtTime(e.ts) + '</td><td>' + esc(e.user) + '</td><td>' + esc(e.type) +
          '</td><td class="num">' + (e.type === 'debit' ? '−' : '+') + fmt(e.amount) + '</td><td class="dim">' + esc(reasons[e.reason] || e.reason) +
          '</td><td class="dim">' + esc(e.bet || '') + '</td></tr>';
      }).join('') : '<tr><td class="dim">No coin movements yet.</td></tr>';
  };
  T.loadClips = async function () {
    if (typeof GP.loadClipList === 'function') { try { GP.loadClipList(); } catch (e) {} }
  };

  // ---------------------------------------------------------------- settings
  T.buildSettings = function () {
    var self = this, html = '';
    this.dirty = {};
    this.def.settings.forEach(function (f) {
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
    this.def.settings.forEach(function (f) {
      var el = self.$('s-' + f[0]);
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
    this.def.settings.forEach(function (f) {
      if (self.dirty[f[0]]) return;
      var el = self.$('s-' + f[0]);
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
    this.def.settings.forEach(function (f) {
      var el = self.$('s-' + f[0]);
      if (f[2] === 'bool') out[f[0]] = el.checked;
      else if (f[2] === 'int' || f[2] === 'num' || f[2] === 'category') out[f[0]] = num(el.value, self.cfg[f[0]]);
      else if (f[2] === 'text') out[f[0]] = cut(el.value.trim(), f[3]);   // what games.py keeps (characters)
      else out[f[0]] = el.value;
    });
    var body = {}; body[this.key] = out;
    var r = await this.req('POST', '/games/api/config', body);
    if (r.ok) { this.dirty = {}; this.$('dirty').textContent = ''; toast('Settings saved'); if (this.def.afterSave) this.def.afterSave(this); }
    else toast(r.d.error || 'save failed');
  };

  // The game is being uninstalled (the page removes the tab right after this): stop everything that
  // would keep running against it.
  T.destroy = function () {
    clearInterval(this.timer);
    if (this.ed && this.ed.close) this.ed.close();
    var v = this.mir;
    if (v) {
      if (v.ro) v.ro.disconnect();
      if (v.inst) { var inst = v.inst; v.inst = null; safe(function () { inst.destroy(); }); }
    }
    var st = document.getElementById('rp-css-' + this.key);
    if (st) st.remove();
    if (window.RoundPanels) delete window.RoundPanels[this.key];
  };

  // ======================================================================
  // registration
  // ======================================================================
  function register(def) {
    if (!$('tab-' + def.key)) return;
    if (def.css) { var st = document.createElement('style'); st.id = 'rp-css-' + def.key; st.textContent = def.css; document.head.appendChild(st); }
    var tab = new Tab(def);
    window.GamePanels = window.GamePanels || {};
    window.RoundPanels = window.RoundPanels || {};     // debug / test hook: RoundPanels.KEY.openEditor()
    window.RoundPanels[def.key] = { tab: tab, openEditor: function () { tab.openEditor(); } };
    window.GamePanels[def.key] = {
      onConfig: function (c) { tab.onConfig(c); }, onState: function (s) { tab.onState(s); },
      onStop: function (k) { tab.onStop(k); }, onLedger: function (e) { tab.onLedger(e); },
      onLink: function (on) { tab.onLink(on); }, onTab: function (k) { tab.onTab(k); },
      onRemove: function () { tab.destroy(); }
    };
    // catch up on what the page's socket already delivered
    var last = GP.last || {};
    if (last.config) tab.onConfig(last.config);
    if (last.states && last.states[def.key]) tab.onState(last.states[def.key]);
    if (last.link != null) tab.onLink(last.link);
  }

  // what the game files need besides register(): their helpers are these
  GP.round = { register: register, num: num, clamp: clamp, fmt: fmt, signed: signed, sum: sum, roundState: roundState,
    cut: cut, isObj: isObj };
})();
