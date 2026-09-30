/*
 * Hexcast Games — Trivia panel tab                                trivia_panel.js
 *
 * The Trivia tab of the Games panel (section#tab-trivia): bet before you see the question, then answer;
 * winners ride or cash out. The panel itself (OBS card, live mirror, controls, play as, players, history,
 * ledger, settings, placement & look editor, API card) is round_common.js, shared with Russian Roulette;
 * this file is the game: its defaults, settings, sample game for the editor, the channel's own questions
 * (lore) and question bank, wording and API rows.
 *
 * Plain browser script. games_panel.html loads it after round_common.js and the trivia.js renderer
 * (window.HexGames.trivia) when Trivia is installed. It needs nothing of Russian Roulette.
 */
(function () {
  'use strict';

  var GP = window.GamesPage || {};
  var RC = GP.round;
  if (!RC) return;
  var esc = GP.esc, toast = GP.toast, num = RC.num, clamp = RC.clamp, fmt = RC.fmt, signed = RC.signed, sum = RC.sum,
      cut = RC.cut, roundState = RC.roundState;

  // Only Trivia's markup uses these (the rest of the panel's rules are round_common.js's).
  var CSS = [
    '.rp-letters button{min-width:44px}',
    '.rp-lore{max-height:360px;overflow:auto}',
    '.rp-lore td{vertical-align:top}',
    '.rp-lore .q{color:var(--ink)} .rp-lore .a{color:var(--good);font-size:12px} .rp-lore .w{color:var(--dim);font-size:12px}',
    '.rp-bank{font-size:13px;color:var(--dim);margin-top:8px}',
    '.rp-bank b{color:var(--ink)} .rp-bank .err{color:var(--warn)}'
  ].join('\n');

  // What the game looks like by default: the editor's Reset (= games.py DEFAULTS).
  var LOOK = { x: 50, y: 50, scale: 1, theme: 'hex', title: 'TRIVIA', lore_label: 'Channel Lore', show_rules: true,
      show_players: true, players_max: 8, sfx: true, sfx_volume: 0.5 };

  // ======================================================================
  // the editor's sample game and demo question
  // ======================================================================
  // trivia_plan() of games.py ("mixed" cycles instead of drawing at random)
  function triviaPlan(n, mode) {
    var D3 = ['easy', 'medium', 'hard'], out = [], i;
    if (D3.indexOf(mode) >= 0) { for (i = 0; i < n; i++) out.push(mode); return out; }
    if (mode === 'mixed') { for (i = 0; i < n; i++) out.push(D3[i % 3]); return out; }
    var e = Math.floor((n + 2) / 3), m = Math.floor((n + 1) / 3);
    for (i = 0; i < n; i++) out.push(i < e ? 'easy' : i < e + m ? 'medium' : 'hard');
    return out;
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

  // ======================================================================
  // the game
  // ======================================================================
  RC.register({
    key: 'trivia', title: 'Trivia',
    // the phase names in the state line, the editor's theme list
    phases: { idle: 'idle', betting: 'bets open', question: 'question open', votes: 'answers locked', reveal: 'reveal', over: 'game over' },
    themes: [['hex', 'Hex — Hexcast red'], ['gameshow', 'Game show — blue & gold'], ['neon', 'Neon']],
    // The only keys a /preview's `overrides` may carry — and what the Edit-Mode editor edits
    // (= games.py APPEARANCE). `look` = their defaults: the editor's Reset.
    appearance: ['x', 'y', 'scale', 'theme', 'title', 'lore_label', 'show_rules', 'show_players', 'players_max', 'sfx', 'sfx_volume'],
    look: LOOK,
    base: [1120, 630],                                   // the renderer's BASE_W x BASE_H (if it is not loaded)
    // The game card's own text fields, in characters (code points, like games.py - no maxlength, which
    // counts UTF-16 units: an emoji is 2): a lore question's category (_qtext(category, 60)), its text and
    // answer, the player.
    fieldMax: { lcat: 60, pu: 40, lq: 300, lc: 120 },
    // [key, label, kind, extra, title]
    settings: [
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
    ],
    textRows: {
      title: ['Title', 'Your branding: the board\'s name (header + the card between games)'],
      lore_label: ['Lore', 'What the overlay calls your own questions (the badge on a lore question)']
    },
    hint: 'Live mirror of the overlay. Bet before you see the question, then answer. Questions come from Open Trivia DB and your own lore. Winners ride or cash out between questions. Every bet is against the bank (your bot).',
    scene: 'board', ledgerNote: '', doc: 'trivia',
    starting: 'getting question 1…',
    demoWord: 'question',
    css: CSS,

    // ---- markup ----
    controls: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr;gap:9px;margin-top:14px">' +
        '<label class="f">Questions <input type="number" id="' + id('nq') + '" min="1" max="50" placeholder="15"></label>' +
        '<label class="f">Lore <select id="' + id('lorem') + '"><option value="">as in settings</option><option value="mixed">mixed in</option><option value="only">lore only</option><option value="off">off</option></select></label></div>' +
        '<div class="row"><button class="act" id="' + id('start') + '">Start game</button>' +
        '<button class="sec" id="' + id('test') + '" title="Plays exactly the same, but writes nothing to the ledger">Test game</button></div>' +
        '<div class="rp-bank" id="' + id('bank') + '">question bank: …</div>';
    },
    play: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr;gap:9px">' +
        '<label class="f">User <input id="' + id('pu') + '" placeholder="viewer" autocomplete="off"></label>' +
        '<label class="f">Amount <input type="number" id="' + id('pa') + '" min="1" step="1" placeholder="coins"></label></div>' +
        '<div class="row"><button class="act" id="' + id('bet') + '">Bet</button><button class="sec" id="' + id('ride') + '">Ride</button>' +
        '<button class="sec" id="' + id('cash') + '">Cash out</button></div>' +
        '<div class="row rp-letters" id="' + id('letters') + '"><span class="note">Answer:</span>' +
        ['A', 'B', 'C', 'D', 'E'].map(function (l) { return '<button class="sec" data-a="' + l + '">' + l + '</button>'; }).join('') + '</div>';
    },
    // the channel's own questions (lore): a card between Players and History
    extra: function (id) {
      return '<div class="card"><h2><span id="' + id('lorename') + '">' + esc(LOOK.lore_label) + '</span> <span class="pill" id="' + id('lorecount') + '">…</span></h2>' +
      '<p class="hint">Your own questions (the lore), mixed into games (or a lore-only game). 2–5 options: the right answer plus 1–4 wrong ones. A question isn\'t asked again for <b>Don\'t repeat</b> hours. The overlay calls them by <b>Name of your own questions</b> (Settings, or Edit Mode); a question without a category shows that name as its category.</p>' +
      '<div class="grid" style="grid-template-columns:2fr 1fr;gap:9px">' +
      '<label class="f">Question <input id="' + id('lq') + '" placeholder="What colour is the streamer\'s hat?"></label>' +
      '<label class="f">Right answer <input id="' + id('lc') + '" placeholder="Red"></label>' +
      '<label class="f">Wrong answers (one per line, 1–4) <textarea id="' + id('lw') + '" rows="3" placeholder="Blue&#10;Green&#10;Gold"></textarea></label>' +
      '<div style="display:flex;flex-direction:column;gap:9px"><label class="f">Difficulty <select id="' + id('ld') + '"><option value="easy">easy</option><option value="medium" selected>medium</option><option value="hard">hard</option></select></label>' +
      '<label class="f">Category <input id="' + id('lcat') + '" placeholder="' + esc(LOOK.lore_label) + '"></label></div></div>' +
      '<div class="row"><button class="act" id="' + id('ladd') + '">Add question</button><span class="rp-out" id="' + id('lout') + '"></span></div>' +
      '<details style="margin-top:10px"><summary class="note">Import JSON</summary><textarea id="' + id('limp') + '" rows="5" style="margin-top:8px" placeholder=\'[{"question":"…","correct":"…","incorrect":["…","…"],"difficulty":"easy"}]\'></textarea>' +
      '<div class="row"><button class="sec" id="' + id('limpgo') + '">Import</button></div></details>' +
      '<div class="rp-lore" style="margin-top:12px"><table class="tb" id="' + id('lorelist') + '"></table></div></div>';
    },
    apiRows: function (a) {
      return [
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
    },
    examples: function (h) {
      return [
        'curl -X POST ' + h + '/start',
        'curl "' + h + '/bet?user=alice&amount=100"',
        'curl "' + h + '/answer?user=alice&answer=B"',
        'curl "' + h + '/ride?user=alice"',
        'curl "' + h + '/cashout?user=alice"',
        'curl -X POST ' + h + '/lore -H "Content-Type: application/json" -d "{\\"question\\":\\"What colour is the streamer\'s hat?\\",\\"correct\\":\\"Red\\",\\"incorrect\\":[\\"Blue\\",\\"Green\\"],\\"difficulty\\":\\"easy\\"}"'].join('\n');
    },

    // ---- wiring ----
    startBody: function (b, $$, field) {
      var n = parseInt($$('nq').value, 10), lm = $$('lorem').value;
      if (n > 0) b.questions = n;
      if (lm) b.lore = lm;
    },
    wire: function (self, $$, who, pout, field) {
      if ($$('ride')) $$('ride').onclick = function () { self.act('/ride', { user: who() }, 'Riding', pout); };
      if ($$('letters')) $$('letters').addEventListener('click', function (e) {
        var b = e.target.closest('button[data-a]'); if (!b) return;
        self.act('/answer', { user: who(), answer: b.dataset.a }, function (d) { return 'Answered ' + d.answer + (d.bettor ? '' : ' (not a bettor: not paid)'); }, pout);
      });
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
      $$('bank').addEventListener('click', async function (e) {
        if (!e.target.closest('button[data-clear]')) return;
        if (!confirm('Start a "new night"? Questions already asked may be asked again.')) return;
        var r = await self.req('POST', self.api + '/asked/clear');
        if (r.ok) { toast('Cleared ' + r.d.cleared + ' asked questions'); self.renderBank(r.d); }
      });
    },
    // the panel names the lore the way the overlay does
    onConfig: function (tab) {
      var name = String(tab.cfgFull().lore_label || LOOK.lore_label);
      tab.$('lorename').textContent = name;
      tab.$('lcat').placeholder = name;
    },
    onFirstTab: function (tab) { tab.loadLore(); tab.loadBank(); tab.loadCategories(); },
    afterSave: function (tab) { tab.loadBank(); },

    // ---- readouts ----
    sub: function (tab, g) {
      return 'question ' + g.number + ' of ' + g.total + (g.next ? ' · ' + tab.catName(g.next) + ' (' + g.next.difficulty + ')' : '') + (g.test ? ' · TEST GAME' : '');
    },
    facts: function (tab, g, cur) {
      var facts;
      var q = g.question;
      facts = [['On the line', fmt(g.on_the_line) + ' ' + esc(cur)], ['Waiting (won)', fmt(g.waiting) + ' ' + esc(cur)],
        ['Answers in', fmt(g.voters)], ['Question', q ? esc(cut(q.text, 80)) : (g.next ? esc(tab.catName(g.next)) : '—')]];
      if (q && q.answer != null) facts.push(['Answer', esc('ABCDE'[q.answer] + ': ' + q.options[q.answer])]);
      return facts;
    },
    idleFacts: function (idle) {
      return [['Next game', idle.questions + ' questions'], ['Difficulty', esc(idle.difficulty)], ['Lore', esc(idle.lore)]];
    },
    playerRows: function (ps) {
      return '<tr><th>User</th><th>Status</th><th class="num">Balance</th><th class="num">Put in</th><th class="num">Streak</th><th class="num">If right</th></tr>' +
        ps.map(function (p) { return '<tr><td>' + esc(p.user) + '</td><td>' + (p.status === 'won' ? 'won · ride or cash out' : (p.fresh ? 'bet' : 'riding')) + (p.answered ? ' ✔' : '') + '</td><td class="num">' + fmt(p.balance) + '</td><td class="num">' + fmt(p.basis) + '</td><td class="num">' + p.streak + '</td><td class="num">' + (p.if_right != null ? fmt(p.if_right) : '—') + '</td></tr>'; }).join('');
    },
    historyBits: function (s) {
      return [['Games', s.games], ['Complete', s.complete], ['Questions', s.questions], ['Right', s.right], ['Wrong', s.wrong], ['Total bet', fmt(s.total_bet)], ['Paid', fmt(s.total_paid)], ['House net', signed(s.house_net)]];
    },

    // ---- the editor ----
    sample: tvSample, preview: tvPreview,

    // ---- the tab's own methods (mixed into the tab) ----
    methods: {
      // A trivia question's category as the overlay names it: a lore question without one
      // (category "") goes by the lore label - the one on screen, so a Test in OBS' too.
    catName: function (h) {
        return h.category || (h.source === 'lore' ? String(this.mirrorEffective().lore_label || LOOK.lore_label) : '?');
    },
    loadLore: async function () {
        var r = await this.req('GET', this.api + '/lore');
        if (!r.ok) return;
        var qs = r.d.questions || [];
        this.$('lorecount').textContent = qs.length + ' question' + (qs.length === 1 ? '' : 's');
        this.$('lorelist').innerHTML = qs.length ? '<tr><th>Question</th><th>Diff.</th><th></th></tr>' + qs.slice().reverse().map(function (q) {
          return '<tr><td><div class="q">' + esc(q.question) + '</div><div class="a">✓ ' + esc(q.correct) + '</div><div class="w">✗ ' + q.incorrect.map(esc).join(' · ') +
            '</div></td><td>' + esc(q.difficulty) + '</td><td class="act"><button class="sec" data-del="' + esc(q.id) + '">Delete</button></td></tr>';
        }).join('') : '<tr><td class="note">No lore questions yet — add the channel\'s own questions above.</td></tr>';
    },
    loadBank: async function () { var r = await this.req('GET', this.api + '/bank'); if (r.ok) this.renderBank(r.d); },
    renderBank: function (b) {
        var p = b.pool || {};
        this.$('bank').innerHTML = 'Question pool: <b>' + (p.easy || 0) + '</b> easy · <b>' + (p.medium || 0) + '</b> medium · <b>' + (p.hard || 0) +
          '</b> hard (more are fetched as needed) · lore <b>' + b.lore_unasked + '</b>/' + b.lore + ' unasked · asked recently <b>' + b.asked + '</b> ' +
          '<button class="sec" data-clear="1" style="padding:3px 9px;font-size:12px">New night</button>' +
          (b.last_error ? '<div class="err">' + esc(b.last_error) + '</div>' : '');
    },
    loadCategories: async function () {
        var r = await this.req('GET', this.api + '/categories');
        var sel = this.$('s-category');
        if (!sel || !r.ok) return;
        var cur = String(this.cfg.category || 0);
        sel.innerHTML = '<option value="0">Any category</option>' + (r.d.categories || []).map(function (c) {
          return '<option value="' + c.id + '">' + esc(c.name) + '</option>';
        }).join('');
        sel.value = cur;
    },
    }
  });
})();
