/*
 * Hexcast Games — Trivia renderer                            static/games/trivia.js
 *
 * Shared by the OBS overlay (/games/overlay) and the control panel (/games#trivia).
 * Plain browser script: no modules, no dependencies, no build step. DOM + CSS only.
 *
 * Registers window.HexGames.trivia:
 *
 *   BASE_W, BASE_H            board size in stage px at scale 1 (1120 x 630)
 *   THEMES                    { hex, gameshow, neon }
 *   DEFAULTS, APPEARANCE      same values / keys as the backend
 *   create(container, config, opts) -> instance      opts: {sound: true}
 *
 * Branding: config `title` is the board's name (header + idle card, "TRIVIA") and
 * `lore_label` what the channel's own questions are called ("Channel Lore"). Both are
 * shown as text (never HTML); the header / idle title and the betting card's lore line
 * shrink to fit, and a long unbroken label wraps (never widens the board).
 *
 *   instance.setConfig(cfg)   instance.resize()    instance.setState(STATE)
 *   instance.reset()          instance.destroy()
 *
 * setState() takes the game's whole STATE ({state, visible, game, idle, ...}); the
 * host has already corrected game.ends_in_ms / elapsed_ms for the time the message
 * waited. The correct answer only exists in the STATE from the reveal on.
 *
 * Phases: betting (rules box, the next question's number / category / difficulty, a
 * "bets close in 10" warning) -> question (the question, its 2-5 options flip in) ->
 * votes (how many picked each option) -> reveal (the right one lights up green, the
 * wrong ones drop away, winners) -> ... -> over (the game's winners).
 */
(function (root) {
  'use strict';

  var HG = root.HexGames || (root.HexGames = {});
  var BASE_W = 1120, BASE_H = 630;
  var LETTERS = 'ABCDE';

  var DEFAULTS = {
    x: 50, y: 50, scale: 1.0, theme: 'hex', title: 'TRIVIA', lore_label: 'Channel Lore',
    show_rules: true, show_players: true, players_max: 8,
    sfx: true, sfx_volume: 0.5, hide_when_idle: true, commands_text: '', questions: 15, difficulty: 'ramp',
    category: 0, lore: 'mixed', lore_every: 5, repeat_hours: 12, open_bet_seconds: 30, between_seconds: 15,
    answer_seconds: 15, show_votes: 'before_reveal', votes_seconds: 3, reveal_seconds: 5, summary_seconds: 10,
    pay_easy: 1.5, pay_medium: 2.0, pay_hard: 3.0, streak_bonus_pct: 10, max_multiplier: 50,
    currency: 'coins', min_bet: 1, max_bet: 100000, question_clip: '', reveal_clip: ''
  };
  var APPEARANCE = ['x', 'y', 'scale', 'theme', 'title', 'lore_label', 'show_rules', 'show_players', 'players_max',
    'sfx', 'sfx_volume'];

  var THEMES = {
    hex: {
      bg: 'radial-gradient(120% 90% at 50% 0%, #2a0f12 0%, #120709 55%, #07040a 100%)',
      panel: 'rgba(16,8,12,.9)', edge: 'linear-gradient(90deg,#ff5a4a,#b3161c)', face: 'linear-gradient(180deg,#26121a,#12080d)',
      ink: '#fff3ef', dim: '#d6aaa4', accent: '#ff5a4a', gold: '#ffc857', good: '#39d98a', bad: '#ff4f4f',
      easy: '#39d98a', medium: '#ffb938', hard: '#ff4f4f', font: "'Bahnschrift','Segoe UI',sans-serif", glow: '0 0 18px rgba(255,90,74,.55)'
    },
    gameshow: {
      bg: 'radial-gradient(120% 100% at 50% 0%, #17307a 0%, #081645 50%, #020615 100%)',
      panel: 'rgba(3,10,40,.9)', edge: 'linear-gradient(90deg,#c9d6ff,#7d8fc4 50%,#c9d6ff)', face: 'linear-gradient(180deg,#0b2a7a,#051650)',
      ink: '#ffffff', dim: '#a9b8e8', accent: '#f5c542', gold: '#f5c542', good: '#33d17a', bad: '#ff5757',
      easy: '#33d17a', medium: '#f5a742', hard: '#ff5757', font: "'Segoe UI','Arial',sans-serif", glow: '0 0 18px rgba(245,197,66,.45)'
    },
    neon: {
      bg: 'radial-gradient(120% 100% at 50% 0%, #2a0a3d 0%, #0f0418 55%, #050208 100%)',
      panel: 'rgba(12,4,20,.9)', edge: 'linear-gradient(90deg,#ff3fb4,#00f0ff)', face: 'linear-gradient(180deg,#1b0a2a,#0b0412)',
      ink: '#fff4fd', dim: '#c9a7e0', accent: '#00f0ff', gold: '#ffe14f', good: '#5cffb0', bad: '#ff3f6a',
      easy: '#5cffb0', medium: '#ffe14f', hard: '#ff3f6a', font: "'Bahnschrift','Segoe UI',sans-serif", glow: '0 0 16px rgba(0,240,255,.7)'
    }
  };

  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function merge(a, b) { var o = {}, k; for (k in a) if (has(a, k)) o[k] = a[k]; if (b) for (k in b) if (has(b, k) && b[k] != null) o[k] = b[k]; return o; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmt(n) { n = Math.round(+n || 0); return (n < 0 ? '−' : '') + String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
  function now() { return (root.performance && root.performance.now) ? root.performance.now() : Date.now(); }
  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }
  // A config text (title, lore label): trimmed, never empty (the default instead).
  function label(v, d) { v = String(v == null ? '' : v).trim(); return v || d; }
  // Font size for a title of this length (characters, not UTF-16 units: an emoji is one):
  // full size up to `full` characters, then smaller.
  function fit(text, px, full) { var n = Array.from(String(text)).length; return n <= full ? px : Math.max(Math.round(px * 0.55), Math.round(px * full / n)); }
  // A difficulty's colour, as the theme's CSS variable (markup stays the same across themes).
  function diffVar(d) { return 'var(--tv-' + (d === 'easy' || d === 'hard' ? d : 'medium') + ')'; }
  // The question ladder shrinks - and splits into two rows past 20 questions - so even a
  // 50-question game fits between the title (at most 440 px) and the timer ring:
  // [hexes per row, hex width, gap, font size].
  function ladderFit(n) {
    if (n <= 15) return [n, 26, 5, 11];
    if (n <= 20) return [n, 20, 4, 10];
    var per = Math.ceil(n / 2);
    return n <= 30 ? [per, 22, 4, 10] : [per, 16, 3, 9];
  }
  var raf = root.requestAnimationFrame ? function (f) { return root.requestAnimationFrame(f); } : function (f) { return setTimeout(function () { f(now()); }, 50); };
  var caf = root.cancelAnimationFrame ? function (id) { root.cancelAnimationFrame(id); } : function (id) { clearTimeout(id); };

  // ------------------------------------------------------------------ sound
  function Sfx() { this.ctx = null; this.vol = 0.5; }
  Sfx.prototype.ac = function () {
    if (!this.ctx) {
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) return null;
      try { this.ctx = new AC(); } catch (e) { return null; }
    }
    if (this.ctx.state === 'suspended') { try { this.ctx.resume(); } catch (e) {} }
    return this.ctx;
  };
  Sfx.prototype.tone = function (freq, dur, gain, type, delay, to) {
    var c = this.ac(); if (!c) return;
    var t = c.currentTime + (delay || 0);
    var o = c.createOscillator(); o.type = type || 'sine'; o.frequency.setValueAtTime(freq, t);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t + dur);
    var g = c.createGain(); g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(gain * this.vol, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(c.destination); o.start(t); o.stop(t + dur + 0.02);
  };
  Sfx.prototype.play = function (name) {
    var s = this;
    switch (name) {
      case 'question': [392, 523, 659, 784].forEach(function (f, i) { s.tone(f, 0.22, 0.2, 'triangle', i * 0.09); }); break;
      case 'tick': this.tone(1200, 0.05, 0.12, 'square'); break;
      case 'lock': this.tone(196, 0.6, 0.35, 'sine'); this.tone(294, 0.6, 0.2, 'triangle', 0.02); break;
      case 'reveal': [523, 659, 784, 1047].forEach(function (f, i) { s.tone(f, 0.35, 0.22, 'sine', i * 0.07); }); break;
      case 'open': this.tone(660, 0.15, 0.18, 'triangle'); this.tone(990, 0.2, 0.15, 'triangle', 0.12); break;
      case 'end': [784, 659, 523, 784, 1047].forEach(function (f, i) { s.tone(f, 0.3, 0.2, 'triangle', i * 0.12); }); break;
    }
  };

  // ------------------------------------------------------------------ styles
  var CSS_DONE = false;
  function injectCss() {
    if (CSS_DONE || !root.document) return;
    CSS_DONE = true;
    var L = 'polygon(26px 0,calc(100% - 26px) 0,100% 50%,calc(100% - 26px) 100%,26px 100%,0 50%)';
    var css = [
      '.htv{position:absolute;inset:0;font-family:var(--tv-font);color:var(--tv-ink);pointer-events:none;border-radius:22px;background:var(--tv-bg);box-shadow:0 18px 50px rgba(0,0,0,.55),inset 0 0 0 2px rgba(255,255,255,.07);overflow:hidden}',
      '.htv *{box-sizing:border-box}',
      '.htv-head{position:absolute;left:22px;right:22px;top:14px;height:64px;display:flex;align-items:center;gap:16px}',
      // the logo keeps its size (the name ellipsizes past 440 px); the ladder takes what is left.
      // The name's clip box gets room for the glow (padding, cancelled by negative margins -
      // max-width includes it: border-box), else overflow:hidden cuts the glow into a box.
      '.htv-logo{display:flex;align-items:center;gap:10px;font-weight:900;font-size:26px;letter-spacing:.08em;white-space:nowrap;text-shadow:var(--tv-glow);flex:0 0 auto;min-width:0}',
      '.htv-logo svg{width:38px;height:38px;flex:none}',
      '.htv-name{overflow:hidden;text-overflow:ellipsis;padding:14px 20px;margin:-14px -20px;max-width:480px}',
      '.htv-ladder{flex:1 1 0;min-width:0;align-self:stretch;overflow:hidden;display:grid;grid-template-columns:repeat(var(--hx-per,1),var(--hx-w,26px));gap:var(--hx-gap,5px);justify-content:center;align-content:center}',
      '.htv-hx{width:var(--hx-w,26px);height:var(--hx-h,29px);clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);display:flex;align-items:center;justify-content:center;font-size:var(--hx-font,11px);font-weight:800;color:#0b0b0b;opacity:.45;transition:transform .3s,opacity .3s}',
      '.htv-hx.done{opacity:.85} .htv-hx.cur{opacity:1;transform:scale(1.35);box-shadow:none;filter:drop-shadow(0 0 6px #fff)}',
      '.htv-ladder.htv-two .htv-hx.cur{transform:scale(1.2)}',
      '.htv-ring{position:relative;width:62px;height:62px;flex:none}',
      '.htv-ring svg{position:absolute;inset:0;transform:rotate(-90deg)}',
      '.htv-ring span{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:900;font-variant-numeric:tabular-nums}',
      '.htv-ring.last span{color:var(--tv-bad);animation:htv-pulse .5s ease-in-out infinite alternate}',
      '@keyframes htv-pulse{from{transform:scale(1)}to{transform:scale(1.18)}}',
      '.htv-main{position:absolute;left:22px;top:92px;bottom:44px;right:22px}',
      '.htv.side .htv-main{right:292px}',
      '.htv-side{position:absolute;right:22px;top:92px;bottom:44px;width:254px;background:var(--tv-panel);border-radius:16px;padding:12px 14px;border:1px solid rgba(255,255,255,.08);overflow:hidden}',
      '.htv-side h4,.htv-card h4{margin:0 0 8px;font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--tv-accent)}',
      '.htv-p{display:flex;align-items:center;gap:6px;font-size:15px;padding:4px 0;border-top:1px solid rgba(255,255,255,.06)}',
      '.htv-p u{text-decoration:none;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.htv-p i{font-style:normal;font-weight:800;font-variant-numeric:tabular-nums}',
      '.htv-p .st{font-size:12px;color:var(--tv-dim);min-width:18px;text-align:center}',
      '.htv-p.won i{color:var(--tv-good)} .htv-p .fire{color:var(--tv-gold);font-size:12px}',
      '.htv-empty{color:var(--tv-dim);font-size:14px;padding:6px 0}',
      '.htv-foot{position:absolute;left:24px;right:24px;bottom:12px;height:22px;display:flex;gap:16px;align-items:center;font-size:12px;color:var(--tv-dim)}',
      '.htv-foot .cmd{flex:1;font-size:14px;color:var(--tv-ink);font-weight:700}',
      '.htv-foot .test{color:var(--tv-bad);font-weight:900;letter-spacing:.2em}',
      /* lozenges */
      '.htv-loz{position:relative;clip-path:' + L + ';background:var(--tv-edge);padding:3px}',
      '.htv-loz>div{clip-path:' + L + ';background:var(--tv-face);padding:14px 44px;height:100%;display:flex;align-items:center;justify-content:center;text-align:center}',
      '.htv-q{height:150px;font-weight:800;line-height:1.22;animation:htv-slide .5s cubic-bezier(.2,.9,.3,1.1) both}',
      '@keyframes htv-slide{from{opacity:0;transform:translateY(-26px) scaleX(.9)}to{opacity:1;transform:none}}',
      '.htv-meta{display:flex;gap:10px;justify-content:center;margin:0 0 10px;font-size:14px;font-weight:700;color:var(--tv-dim)}',
      '.htv-pill{padding:3px 12px;border-radius:99px;font-size:13px;font-weight:900;letter-spacing:.08em;text-transform:uppercase;color:#0b0b0b}',
      '.htv-pill.lore{background:var(--tv-gold)}',
      '.htv-opts{display:grid;grid-template-columns:1fr 1fr;gap:14px 22px;margin-top:20px}',
      '.htv-opt{height:74px;animation:htv-flip .45s cubic-bezier(.2,.9,.3,1.2) both}',
      '.htv-opt.span{grid-column:1 / span 2;width:calc(50% - 11px);justify-self:center}',
      '.htv-opt>div{justify-content:flex-start!important;padding:0 40px!important;gap:12px;font-weight:700}',
      '.htv-opt b{color:var(--tv-gold);font-size:22px;font-weight:900;flex:none}',
      '.htv-opt .t{flex:1;text-align:left;line-height:1.15}',
      '.htv-opt .n{font-size:15px;font-weight:900;color:var(--tv-ink);opacity:0;transition:opacity .3s;font-variant-numeric:tabular-nums;flex:none}',
      '.htv-opt .bar{position:absolute;left:3px;top:3px;bottom:3px;width:0;background:rgba(255,255,255,.12);transition:width .8s ease;clip-path:inherit}',
      '.htv-opt.votes .n{opacity:1}',
      '@keyframes htv-flip{from{opacity:0;transform:rotateX(90deg)}to{opacity:1;transform:none}}',
      '.htv-opt.right{background:linear-gradient(90deg,#9dffc9,var(--tv-good));animation:htv-win .6s ease both;filter:drop-shadow(0 0 14px var(--tv-good))}',
      '.htv-opt.right>div{background:linear-gradient(180deg,#1d7a4a,#0f4f2e)}',
      '.htv-opt.right b{color:#fff}',
      '@keyframes htv-win{0%{transform:scale(1)}40%{transform:scale(1.06)}100%{transform:scale(1.03)}}',
      '.htv-opt.wrong{animation:htv-drop .9s cubic-bezier(.5,0,.8,.4) both}',
      '.htv-opt.wrong.l{--rot:-5deg} .htv-opt.wrong.r{--rot:5deg}',
      '@keyframes htv-drop{0%{opacity:1;transform:none}100%{opacity:.18;transform:translateY(18px) rotate(var(--rot,4deg)) scale(.92)}}',
      '.htv-status{margin-top:30px;text-align:center;font-size:17px;font-weight:700;color:var(--tv-dim)}',
      '.htv-status b{color:var(--tv-ink)} .htv-status .lock{color:var(--tv-accent);letter-spacing:.2em;font-weight:900}',
      '.htv-res{margin-top:14px;display:flex;justify-content:center;gap:10px;flex-wrap:wrap;animation:htv-slide .5s .5s both}',
      '.htv-chip{background:var(--tv-panel);border:1px solid rgba(255,255,255,.1);border-radius:99px;padding:4px 12px;font-size:15px;font-weight:700}',
      '.htv-chip.g{color:var(--tv-good)} .htv-chip.b{color:var(--tv-bad)}',
      /* betting card */
      '.htv-card{background:var(--tv-panel);border-radius:18px;border:1px solid rgba(255,255,255,.08);padding:18px 22px}',
      // minmax(0,1fr): a long category / lore label wraps inside its card, never widens the column
      '.htv-bet{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;height:100%}',
      '.htv-next{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:8px;animation:htv-slide .5s both}',
      '.htv-next .num{font-size:15px;letter-spacing:.3em;color:var(--tv-dim);font-weight:800}',
      '.htv-next .cat{max-width:100%;font-size:34px;font-weight:900;line-height:1.1;text-shadow:var(--tv-glow);overflow-wrap:anywhere}',
      '.htv-next .htv-pill.lore{display:inline-block;max-width:100%;overflow-wrap:anywhere}',
      '.htv-next .pay{font-size:18px;font-weight:700;margin-top:6px}',
      '.htv-next .pay b{color:var(--tv-gold);font-size:26px}',
      '.htv-next .big{font-size:64px;font-weight:900;font-variant-numeric:tabular-nums;line-height:1;margin-top:6px}',
      '.htv-next .big.last{color:var(--tv-bad);animation:htv-pulse .5s ease-in-out infinite alternate}',
      '.htv-next .lbl{font-size:12px;letter-spacing:.2em;color:var(--tv-dim);font-weight:800}',
      '.htv-rules p{margin:6px 0;font-size:16px;line-height:1.3}',
      '.htv-rules em{font-style:normal;color:var(--tv-accent);font-weight:900}',
      '.htv-rules .warn{margin-top:10px;font-size:20px;font-weight:900;color:var(--tv-bad);animation:htv-pulse .5s ease-in-out infinite alternate;transform-origin:left center}',
      '.htv-rules .last{margin-top:10px;font-size:14px;color:var(--tv-dim)}',
      /* summary + idle */
      '.htv-center{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:6px;animation:htv-slide .5s both}',
      '.htv-center h2{margin:0;max-width:100%;font-size:44px;font-weight:900;letter-spacing:.04em;text-shadow:var(--tv-glow);overflow-wrap:anywhere}',
      '.htv-center .sub{color:var(--tv-dim);font-size:17px}',
      '.htv-center .row{display:flex;justify-content:space-between;width:420px;font-size:19px;padding:4px 6px;border-top:1px solid rgba(255,255,255,.08)}',
      '.htv-center .row i{font-style:normal;font-weight:900}',
      '.htv-center .pos{color:var(--tv-good)} .htv-center .neg{color:var(--tv-bad)}',
      '.htv-center .tot{margin-top:10px;display:flex;gap:18px;color:var(--tv-dim);font-size:14px}'
    ].join('\n');
    var st = root.document.createElement('style');
    st.setAttribute('data-hexgames', 'trivia');
    st.textContent = css;
    root.document.head.appendChild(st);
  }

  var HEX_SVG = '<svg viewBox="0 0 40 40"><polygon points="20,2 36,11 36,29 20,38 4,29 4,11" fill="none" stroke="currentColor" stroke-width="3.5"/>' +
    '<text x="20" y="26" text-anchor="middle" font-size="17" font-weight="900" fill="currentColor">?</text></svg>';

  // ------------------------------------------------------------------ instance
  function TV(container, config, opts) {
    injectCss();
    opts = opts || {};
    this.box = container;
    this.soundOn = opts.sound !== false;
    this.sfx = this.soundOn ? new Sfx() : null;
    this.root = root.document.createElement('div');
    this.root.className = 'htv';
    this.root.innerHTML =
      '<div class="htv-head"><div class="htv-logo">' + HEX_SVG + '<span class="htv-name"></span></div>' +
      '<div class="htv-ladder"></div><div class="htv-ring"><svg viewBox="0 0 62 62"><circle cx="31" cy="31" r="27" fill="none" stroke="rgba(255,255,255,.12)" stroke-width="6"/>' +
      '<circle class="arc" cx="31" cy="31" r="27" fill="none" stroke-width="6" stroke-linecap="round"/></svg><span></span></div></div>' +
      '<div class="htv-main"></div><div class="htv-side"><h4>Playing</h4><div class="htv-pb"></div></div>' +
      '<div class="htv-foot"><span class="cmd"></span><span class="credit"></span><span class="test"></span></div>';
    container.appendChild(this.root);
    var q = this.root.querySelector.bind(this.root);
    this.el = { name: q('.htv-name'), ladder: q('.htv-ladder'), ring: q('.htv-ring'), arc: q('.htv-ring .arc'), rnum: q('.htv-ring span'),
      main: q('.htv-main'), side: q('.htv-side'), pb: q('.htv-pb'), cmd: q('.htv-foot .cmd'),
      credit: q('.htv-foot .credit'), test: q('.htv-foot .test') };
    this.keys = {};
    this.done = {};
    this.st = null; this.at = now();
    this.setConfig(config);
    var self = this;
    this._loop = function (t) { if (self.dead) return; self._frame(t); self._raf = raf(self._loop); };
    this._raf = raf(this._loop);
  }
  var P = TV.prototype;

  P.setConfig = function (cfg) {
    this.cfg = merge(DEFAULTS, cfg);
    var th = THEMES[this.cfg.theme] || THEMES.hex, s = this.root.style;
    this.th = th;
    s.setProperty('--tv-bg', th.bg); s.setProperty('--tv-panel', th.panel); s.setProperty('--tv-edge', th.edge);
    s.setProperty('--tv-face', th.face); s.setProperty('--tv-ink', th.ink); s.setProperty('--tv-dim', th.dim);
    s.setProperty('--tv-accent', th.accent); s.setProperty('--tv-gold', th.gold); s.setProperty('--tv-good', th.good);
    s.setProperty('--tv-bad', th.bad); s.setProperty('--tv-font', th.font); s.setProperty('--tv-glow', th.glow);
    s.setProperty('--tv-easy', th.easy); s.setProperty('--tv-medium', th.medium); s.setProperty('--tv-hard', th.hard);
    this.el.arc.setAttribute('stroke', th.accent);
    this.root.querySelector('.htv-logo').style.color = th.accent;
    this.title = label(this.cfg.title, DEFAULTS.title);
    this.lore = label(this.cfg.lore_label, DEFAULTS.lore_label);
    this.el.name.textContent = this.title;
    this.el.name.title = this.title;
    this.el.name.style.fontSize = fit(this.title, 26, 14) + 'px';
    if (this.sfx) this.sfx.vol = clamp(+this.cfg.sfx_volume || 0, 0, 1);
    // No cache reset here: every section is rebuilt only when its markup changes (_set), and
    // neither the theme (CSS variables) nor the title / lore label (_labels) is in it - so a
    // placement / sound change, a Test in OBS starting or ending, or a Save never rebuilds
    // (and re-animates) the question on stream.
  };
  P.resize = function () {};
  P.setState = function (st) { this.st = st && typeof st === 'object' ? st : null; this.at = now(); };
  P.reset = function () { this.st = null; this.keys = {}; };
  P.destroy = function () {
    this.dead = true; caf(this._raf);
    if (this.root.parentNode) this.root.parentNode.removeChild(this.root);
    if (this.sfx && this.sfx.ctx) { try { this.sfx.ctx.close(); } catch (e) {} }
  };

  P._set = function (key, el, html) {
    if (this.keys[key] !== html) { this.keys[key] = html; el.innerHTML = html; this.gen = (this.gen || 0) + 1; return true; }
    return false;
  };
  // The branding texts go into the markup's placeholders as text: .htv-lorel = the lore
  // label (sized to fit where it is the betting card's big category line), .htv-titlel =
  // the title (sized to fit). Only after a rebuild or a new label.
  P._labels = function () {
    var k = (this.gen || 0) + '|' + this.lore + '|' + this.title;
    if (this.keys.labels === k) return;
    this.keys.labels = k;
    var i, ns = this.el.main.querySelectorAll('.htv-lorel');
    for (i = 0; i < ns.length; i++) {
      ns[i].textContent = this.lore;
      if (ns[i].classList.contains('cat')) ns[i].style.fontSize = fit(this.lore, 34, 14) + 'px';
    }
    ns = this.el.main.querySelectorAll('.htv-titlel');
    for (i = 0; i < ns.length; i++) { ns[i].textContent = this.title; ns[i].style.fontSize = fit(this.title, 44, 18) + 'px'; }
  };
  P._fitLadder = function (n) {
    if (this.keys.ladN === n) return;
    this.keys.ladN = n;
    var f = ladderFit(n), s = this.el.ladder.style;
    s.setProperty('--hx-per', String(Math.max(1, f[0])));
    s.setProperty('--hx-w', f[1] + 'px'); s.setProperty('--hx-h', Math.round(f[1] * 1.12) + 'px');
    s.setProperty('--hx-gap', f[2] + 'px'); s.setProperty('--hx-font', f[3] + 'px');
    this.el.ladder.classList.toggle('htv-two', f[0] < n);
  };
  P._snd = function (name) { if (this.sfx && this.cfg.sfx) { try { this.sfx.play(name); } catch (e) {} } };
  P._once = function (key, fresh, name) { if (this.done[key]) return; this.done[key] = 1; if (fresh) this._snd(name); };

  P._frame = function (t) {
    var st = this.st, g = st && st.game, cfg = this.cfg, th = this.th;
    var dt = t - this.at;
    var left = g && g.ends_in_ms != null ? Math.max(0, g.ends_in_ms - dt) : null;
    var el = g ? (+g.elapsed_ms || 0) + dt : 0;
    var fresh = el < 600;
    var ph = g ? g.phase : 'idle';
    this.root.classList.toggle('side', !!cfg.show_players && !!g);
    this.el.side.style.display = cfg.show_players && g ? '' : 'none';
    // ladder
    var plan = g ? g.plan : [], lad = '';
    var results = g ? g.results : [];
    for (var i = 0; i < plan.length; i++) {
      var d = plan[i], cls = i + 1 < g.number || (i + 1 === g.number && (ph === 'reveal' || ph === 'over')) ? 'done' : '';
      if (i + 1 === g.number && ph !== 'over') cls = 'cur';
      lad += '<div class="htv-hx ' + cls + '" style="background:' + diffVar(d) + '">' + esc(results[i] ? results[i].correct : i + 1) + '</div>';
    }
    this._fitLadder(plan.length);
    this._set('ladder', this.el.ladder, lad);
    // timer ring
    var total = g && g.phase_ms ? g.phase_ms : 0;
    var showRing = left != null && total > 0 && (ph === 'betting' || ph === 'question');
    this.el.ring.style.visibility = showRing ? 'visible' : 'hidden';
    if (showRing) {
      var frac = clamp(left / total, 0, 1), C = 2 * Math.PI * 27;
      this.el.arc.setAttribute('stroke-dasharray', (C * frac).toFixed(1) + ' ' + C.toFixed(1));
      this.el.rnum.textContent = Math.ceil(left / 1000);
      var last = left <= (ph === 'betting' ? 10000 : 5000);
      this.el.ring.classList.toggle('last', last);
      this.el.arc.setAttribute('stroke', last ? th.bad : th.accent);
      var sec = Math.ceil(left / 1000);
      if (sec <= 5 && sec >= 1) this._once(g.id + '/' + g.number + '/' + ph + '/t' + sec, true, 'tick');
    }
    // main
    if (!g) this._idle(st);
    else if (ph === 'betting') this._betting(g, left, fresh);
    else if (ph === 'question' || ph === 'votes' || ph === 'reveal') this._question(g, left, fresh);
    else this._over(g, fresh);
    this._labels();
    // players
    if (g && cfg.show_players) this._players(g);
    // foot
    this._set('cmd', this.el.cmd, g && cfg.commands_text ? esc(cfg.commands_text) : '');
    this._set('credit', this.el.credit, g && g.opentdb ? 'Questions: Open Trivia DB (opentdb.com) · CC BY-SA 4.0' : '');
    this._set('test', this.el.test, g && g.test ? 'TEST GAME · NO COINS' : '');
  };

  P._pay = function (g, diff) { return (+g.pays[diff] || 0).toFixed(g.pays[diff] % 1 ? 1 : 0); };

  P._betting = function (g, left, fresh) {
    var n = g.next || { number: g.number, difficulty: g.plan[g.number - 1] };
    this._once(g.id + '/' + g.number + '/open', fresh, 'open');
    var sec = left == null ? '' : Math.ceil(left / 1000);
    var key = 'bet/' + g.id + '/' + g.number + '/' + (n.category || '') + '/' + JSON.stringify(g.last && g.last.number) + '/' + this.cfg.show_rules;
    var waiting = (g.players || []).filter(function (p) { return p.status === 'won'; }).length;
    var first = g.number === 1;
    var lastLine = g.last ? '<div class="last">Question ' + g.last.number + ': the answer was <b>' + esc(g.last.correct) + '</b> · ' +
      g.last.right.length + ' bettor' + (g.last.right.length === 1 ? '' : 's') + ' right</div>' : '';
    var rules = this.cfg.show_rules ? '<div class="htv-card htv-rules"><h4>' + (first ? 'How to play' : 'Ride or cash out?') + '</h4>' +
      (first ? '<p><em>Bet now</em> — before you see the question.</p><p>Everyone can answer · only bets get paid.</p>' +
        '<p>Right answer: pays by difficulty. Wrong or no answer: the bet is lost.</p><p>Winners <em>ride</em> (streak bonus!) or <em>cash out</em>.</p>'
        : '<p>Winners: <em>ride</em> your whole balance on the next question (+' + g.pays.bonus_pct + '% per streak), or <em>cash out</em>.</p>' +
          '<p>No choice = cashed out for you.</p><p>New players: <em>bet now</em>.</p>') +
      lastLine + '<div class="htv-w"></div></div>' : '<div></div>';
    // a lore question without a category (category "") is named by the lore label
    var cat = n.category ? '<div class="cat">' + esc(n.category) + '</div>'
      : n.source === 'lore' ? '<div class="cat htv-lorel"></div>' : '<div class="cat">Mystery category</div>';
    var html = '<div class="htv-bet"><div class="htv-card htv-next">' +
      '<div class="num">QUESTION ' + n.number + ' OF ' + g.total + '</div>' + cat +
      '<div><span class="htv-pill" style="background:' + diffVar(n.difficulty) + '">' + esc(cap(n.difficulty)) + '</span>' +
      (n.source === 'lore' ? ' <span class="htv-pill lore htv-lorel"></span>' : '') + '</div>' +
      '<div class="pay">Right answer pays <b>×' + this._pay(g, n.difficulty) + '</b></div>' +
      '<div class="lbl">' + (first ? 'BETS CLOSE IN' : 'NEXT QUESTION IN') + '</div><div class="big"></div>' +
      '</div>' + rules + '</div>';
    this._set('main', this.el.main, html);
    this.keys.mainKey = key;
    var big = this.el.main.querySelector('.big');
    if (big) { big.textContent = sec; big.classList.toggle('last', left != null && left <= 10000); }
    var w = this.el.main.querySelector('.htv-w');
    if (w) {
      var warn = left != null && left <= 10000 ? '<div class="warn">⏱ Bets close in ' + sec + '!</div>' :
        (waiting ? '<div class="last">' + waiting + ' winner' + (waiting === 1 ? '' : 's') + ' deciding…</div>' : '');
      if (w.innerHTML !== warn) w.innerHTML = warn;
    }
  };

  P._question = function (g, left, fresh) {
    var q = g.question, ph = g.phase;
    if (!q) return;
    var qkey = g.id + '/' + q.number;
    this._once(qkey + '/question', ph === 'question' && fresh, 'question');
    if (ph === 'votes') this._once(qkey + '/lock', fresh, 'lock');
    if (ph === 'reveal') this._once(qkey + '/reveal', fresh, 'reveal');
    var len = q.text.length, fs = len < 60 ? 34 : len < 110 ? 29 : len < 170 ? 24 : 20;
    var n = q.options.length;
    var opts = q.options.map(function (o, i) {
      var ofs = o.length < 22 ? 24 : o.length < 40 ? 20 : 16;
      var span = n % 2 === 1 && i === n - 1 ? ' span' : '';
      return '<div class="htv-loz htv-opt' + span + '" data-i="' + i + '" style="animation-delay:' + (0.35 + i * 0.22) + 's">' +
        '<span class="bar"></span><div><b>' + LETTERS[i] + ':</b><span class="t" style="font-size:' + ofs + 'px">' + esc(o) + '</span><span class="n"></span></div></div>';
    }).join('');
    var html = '<div class="htv-meta"><span>QUESTION ' + q.number + ' OF ' + g.total + '</span>' +
      '<span class="htv-pill" style="background:' + diffVar(q.difficulty) + '">' + esc(cap(q.difficulty)) + '</span>' +
      (q.source === 'lore' ? '<span class="htv-pill lore htv-lorel"></span>' : '<span>' + esc(q.category) + '</span>') + '</div>' +
      '<div class="htv-loz htv-q"><div style="font-size:' + fs + 'px">' + esc(q.text) + '</div></div>' +
      '<div class="htv-opts">' + opts + '</div><div class="htv-status"></div><div class="htv-resb"></div>';
    if (this._set('main', this.el.main, html)) { this.keys.optState = ''; this.keys.status = null; this.keys.res = null; }
    // votes / reveal / status
    var votes = g.votes, total = 0, i;
    if (votes) for (i = 0; i < votes.length; i++) total += votes[i];
    var optState = ph + '/' + JSON.stringify(votes) + '/' + q.answer;
    if (optState !== this.keys.optState) {
      this.keys.optState = optState;
      var nodes = this.el.main.querySelectorAll('.htv-opt');
      for (i = 0; i < nodes.length; i++) {
        var node = nodes[i], nEl = node.querySelector('.n'), bar = node.querySelector('.bar');
        if (votes) {
          node.classList.add('votes');
          var pct = total ? Math.round(votes[i] * 100 / total) : 0;
          nEl.textContent = votes[i] + ' · ' + pct + '%';
          bar.style.width = 'calc(' + pct + '% - 6px)';
        }
        if (ph === 'reveal' && q.answer != null) {
          node.style.animationDelay = '0s';
          if (i === q.answer) node.classList.add('right');
          else node.classList.add('wrong', i % 2 ? 'r' : 'l');
        }
      }
    }
    var status = '';
    if (ph === 'question') status = '<b>' + fmt(g.voters) + '</b> answer' + (g.voters === 1 ? '' : 's') + ' in · everyone can answer, only bets get paid';
    else if (ph === 'votes') status = '<span class="lock">ANSWERS LOCKED</span> · ' + fmt(g.voters) + ' answered';
    else if (ph === 'reveal') status = 'The answer is <b>' + LETTERS[q.answer] + ': ' + esc(q.options[q.answer]) + '</b>';
    this._set('status', this.el.main.querySelector('.htv-status'), status);
    var res = '';
    if (ph === 'reveal' && g.last && g.last.number === q.number) {
      var L = g.last, cur = g.currency;
      res = '<div class="htv-res"><span class="htv-chip g">✓ ' + L.right.length + ' right</span><span class="htv-chip b">✗ ' + L.wrong.length + ' wrong</span>' +
        L.right.slice(0, 4).map(function (r) { return '<span class="htv-chip">' + esc(r.user) + ' ' + fmt(r.balance) + (r.streak > 1 ? ' 🔥' + r.streak : '') + '</span>'; }).join('') +
        (L.right.length > 4 ? '<span class="htv-chip">+' + (L.right.length - 4) + '</span>' : '') + '</div>';
    }
    this._set('res', this.el.main.querySelector('.htv-resb'), res);
  };

  P._over = function (g, fresh) {
    var s = g.summary;
    if (!s) return;
    this._once(g.id + '/end', fresh, 'end');
    var title = s.outcome === 'complete' ? 'Game over!' : s.outcome === 'no_bets' ? 'No bets — no game' : s.outcome === 'walked' ? 'Nobody left playing' : esc(s.text);
    var rows = (s.players || []).slice(0, 6).map(function (p) {
      return '<div class="row"><span>' + esc(p.user) + '</span><i class="' + (p.net > 0 ? 'pos' : p.net < 0 ? 'neg' : '') + '">' +
        (p.net > 0 ? '+' : '') + fmt(p.net) + '</i></div>';
    }).join('');
    var html = '<div class="htv-center"><h2>' + title + '</h2><div class="sub">' + s.questions + ' of ' + s.total + ' questions · ' +
      s.right + ' right answers · ' + s.wrong + ' wrong</div>' + rows +
      '<div class="tot"><span>Bet ' + fmt(s.total_bet) + '</span><span>Paid ' + fmt(s.total_paid) + '</span><span>House ' +
      (s.house_net >= 0 ? '+' : '') + fmt(s.house_net) + '</span></div></div>';
    this._set('main', this.el.main, html);
  };

  P._idle = function (st) {
    var idle = (st && st.idle) || {}, p = idle.pays || {};
    var html = '<div class="htv-center"><h2 class="htv-titlel"></h2>' +
      '<div class="sub">Next game: ' + esc(idle.questions || 15) + ' questions · ' +
      (idle.difficulty === 'ramp' ? 'easy → hard' : esc(idle.difficulty || '')) + (idle.lore && idle.lore !== 'off' ? ' · with <span class="htv-lorel"></span>' : '') + '</div>' +
      '<div class="sub">Right answers pay ×' + (p.easy || 1.5) + ' easy · ×' + (p.medium || 2) + ' medium · ×' + (p.hard || 3) + ' hard · +' +
      (p.bonus_pct || 0) + '% per streak</div></div>';
    this._set('main', this.el.main, html);
  };

  P._players = function (g) {
    var ps = g.players || [], max = +this.cfg.players_max || 8, ph = g.phase, html = '';
    if (!ps.length) html = '<div class="htv-empty">' + (ph === 'betting' ? 'No bets yet' : 'Nobody riding') + '</div>';
    for (var i = 0; i < Math.min(ps.length, max); i++) {
      var p = ps[i];
      var st = p.status === 'won' ? (ph === 'betting' ? '⏳' : '✓') : (ph === 'question' ? (p.answered ? '✔' : '…') : '');
      html += '<div class="htv-p' + (p.status === 'won' ? ' won' : '') + '"><span class="st">' + st + '</span><u>' + esc(p.user) + '</u>' +
        (p.streak > 0 ? '<span class="fire">🔥' + p.streak + '</span>' : '') + '<i>' + fmt(p.balance) + '</i></div>';
    }
    if (ps.length > max) html += '<div class="htv-empty">+' + (ps.length - max) + ' more</div>';
    html += '<div class="htv-empty" style="margin-top:6px">On the line: <b>' + fmt(g.on_the_line) + '</b> ' + esc(g.currency) + '</div>';
    this._set('players', this.el.pb, html);
  };

  function copyObj(o) { var r = {}; for (var k in o) if (has(o, k)) r[k] = o[k]; return r; }
  var API = {
    BASE_W: BASE_W, BASE_H: BASE_H, THEMES: THEMES, DEFAULTS: copyObj(DEFAULTS), APPEARANCE: APPEARANCE.slice(),
    create: function (container, config, opts) { return new TV(container, config, opts); }
  };
  HG.trivia = API;
  if (typeof module === 'object' && module && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
