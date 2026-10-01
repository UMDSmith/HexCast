/*
 * Hexcast Games — Hexfall panel tab                              hexfall_panel.js
 *
 * The Hexfall tab of the Games panel (section#tab-hexfall): a hex-themed Plinko - one token falls
 * through rows of hexagonal pegs into a slot that pays the bet times its multiplier. The panel itself
 * (OBS card, live mirror, controls, play as, players, history, ledger, settings, placement & look editor,
 * API card) is round_common.js, shared with Russian Roulette and Trivia; this file is the game: its
 * defaults, settings, sample game for the editor, wording, API rows and the payout-table card.
 *
 * Plain browser script. games_panel.html loads it after round_common.js and the hexfall.js renderer
 * (window.HexGames.hexfall) when Hexfall is installed. It needs nothing of the other games.
 */
(function () {
  'use strict';

  var GP = window.GamesPage || {};
  var RC = GP.round;
  if (!RC) return;
  var esc = GP.esc, num = RC.num, clamp = RC.clamp, fmt = RC.fmt, signed = RC.signed, sum = RC.sum, roundState = RC.roundState;
  function toast(m, ms) { if (typeof GP.toast === 'function') GP.toast(m, ms); }
  function renderer() { return window.HexGames && window.HexGames.hexfall; }
  function multLabel(m) {
    m = +m || 0;
    if (m === 0) return 'BUST';
    return '×' + (m >= 100 ? String(Math.round(m)) : m >= 10 ? String(Math.round(m * 10) / 10) : String(Math.round(m * 100) / 100));
  }
  function pctLabel(p) { p = +p || 0; return (p >= 10 ? p.toFixed(0) : p >= 1 ? p.toFixed(1) : p >= 0.1 ? p.toFixed(2) : p.toFixed(3)) + '%'; }

  // ======================================================================
  // the editor's sample game and demo drop
  // ======================================================================
  // The multiplier table a config describes: its own list (rows + 1 numbers) or the renderer's built-in
  // table for rows x risk (the same tables as the backend: a test keeps them equal).
  function tableFor(c) {
    var R = renderer(), rows = clamp(Math.round(num(c.rows, 12)), 8, 16), risk = /^(low|medium|high)$/.test(c.risk) ? c.risk : 'medium';
    var custom = Array.isArray(c.multipliers) ? c.multipliers.map(Number) : [];
    var mults = custom.length === rows + 1 && custom.every(function (m) { return isFinite(m) && m >= 0; }) ? custom : (R ? R.tableOf(rows, risk) : [0, 1, 0]);
    var slots = R ? R.slotsOf(rows, mults) : [], rtp = R ? R.rtpOf(slots) : 0;
    return { rows: rows, risk: risk, mults: mults, slots: slots, rtp: rtp, edge: Math.round((100 - rtp) * 100) / 100, source: mults === custom ? 'custom' : 'preset' };
  }

  // sample game (drop 1, bets open): six players with bets on the line
  var SAMPLE = [['alice', 200], ['bob', 25], ['carol', 120], ['dave', 80], ['erin', 25], ['frank', 40]];
  function hfSample(c, gid, ms) {
    var T = tableFor(c), drops = clamp(Math.round(num(c.drops, 3)), 1, 10);
    var ps = SAMPLE.map(function (p) { return { user: p[0], bet: p[1], max_win: Math.floor(p[1] * Math.max.apply(null, T.mults)) }; })
      .sort(function (a, b) { return b.bet - a.bet; });
    var cur = String(c.currency || 'coins'), mid = T.slots.length >> 1;
    var recent = [mid, 2, mid + 1, 0, 5, mid].map(function (k) { return { slot: k, rows: T.rows, mult: (T.slots[Math.min(k, T.slots.length - 1)] || {}).mult || 0 }; });
    var idle = { rows: T.rows, risk: T.risk, source: T.source, drops: drops, slots: T.slots, rtp_pct: T.rtp, house_edge_pct: T.edge, recent: recent, currency: cur };
    return roundState({
      id: gid, test: false, phase: 'betting', ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0,
      drop: 1, drops: drops, rows: T.rows, risk: T.risk, source: T.source, slots: T.slots, rtp_pct: T.rtp, house_edge_pct: T.edge,
      fall: null, hits: [], recent: recent, players: ps, on_the_line: sum(ps, 'bet'), last: null, outcome: null, summary: null,
      currency: cur, min_bet: c.min_bet, max_bet: c.max_bet, commands_text: c.commands_text || ''
    }, idle);
  }
  // ▶ Preview's timings (merged into the renderer's config while it plays): the fall runs on 7 s
  // (the server's shortest is 6) and the result card counts from result_seconds.
  function hfDemoTiming(c) {
    return { drop_seconds: 7, result_seconds: Math.min(clamp(num(c.result_seconds, 5), 2, 30), 3) };
  }
  // [{ms, state}]: the last seconds of the bet window -> the token falling (a random path: a demo, not
  // the server's coin) -> its slot and payouts -> the game-over card. With hfDemoTiming(): 3 + 7 + 3 + 4 s.
  function hfPreview(c, gid) {
    var s0 = hfSample(c, gid, 3000), g0 = s0.game, rows = g0.rows, D = 7;
    var path = [], slot = 0, i;
    for (i = 0; i < rows; i++) { var b = Math.random() < 0.5 ? 0 : 1; path.push(b); slot += b; }
    var mult = g0.slots[slot].mult, seed = Math.floor(Math.random() * 2147483647);
    var fall = { drop: 1, path: path, slot: slot, mult: mult, seed: seed, ms: D * 1000 };
    var res = g0.players.map(function (p) { var paid = Math.floor(p.bet * mult); return { user: p.user, bet: p.bet, paid: paid, net: paid - p.bet }; })
      .sort(function (a, b) { return b.paid - a.paid || b.bet - a.bet; });
    var last = { drop: 1, slot: slot, mult: mult, bust: mult === 0, winners: res.filter(function (r) { return r.net > 0; }),
      even: res.filter(function (r) { return r.net === 0; }), losers: res.filter(function (r) { return r.net < 0; }),
      total_bet: sum(res, 'bet'), total_paid: sum(res, 'paid') };
    var hits = [{ drop: 1, slot: slot, mult: mult }];
    var recent = [{ slot: slot, rows: rows, mult: mult }].concat(g0.recent).slice(0, 12);
    var summary = { outcome: 'complete', text: 'Every drop has fallen', planned: g0.drops, rows: rows, risk: g0.risk, source: g0.source, rtp_pct: g0.rtp_pct,
      drops: [{ drop: 1, slot: slot, rows: rows, mult: mult, bet: last.total_bet, paid: last.total_paid }],
      total_bet: last.total_bet, total_paid: last.total_paid, house_net: last.total_bet - last.total_paid,
      best: res[0] && res[0].net > 0 ? { user: res[0].user, paid: res[0].paid, net: res[0].net, drop: 1, mult: mult } : null,
      players: res.map(function (r) { return { user: r.user, bet: r.bet, paid: r.paid, net: r.net }; }).sort(function (a, b) { return b.net - a.net; }),
      test: false, currency: g0.currency };
    function step(phase, secs, extra) {
      var ms = Math.round(secs * 1000), g = Object.assign({}, g0, { phase: phase, ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0 }, extra);
      return { ms: ms, state: roundState(g, s0.idle) };
    }
    return [
      step('betting', 3),
      step('dropping', D, { fall: fall, players: g0.players }),
      step('result', clamp(num(c.result_seconds, 5), 2, 30), { fall: fall, last: last, hits: hits, recent: recent, players: [] }),
      step('over', 4, { fall: fall, last: last, hits: hits, recent: recent, players: [], outcome: 'complete', summary: summary })
    ];
  }

  // ======================================================================
  // the payout table card (GET /bets: every slot's multiplier, exact odds, RTP and house edge)
  // ======================================================================
  function oddsCard(id) {
    return '<div class="card"><h2>Payout table <span class="pill" id="' + id('oddspill') + '">—</span></h2>' +
      '<p class="hint">Exactly what is paid: at every peg the server flips a fair coin, so slot <i>k</i> has the binomial probability C(rows, <i>k</i>) / 2<sup>rows</sup>. ' +
      '<b>Return</b> is the sum of probability × multiplier and the <b>house edge</b> is what is left - nothing is nudged. Winnings are rounded down to whole coins. ' +
      'Change <b>Rows</b> / <b>Risk</b> or enter your own <b>Custom multipliers</b> in Settings below.</p>' +
      '<div class="rp-out" id="' + id('oddsnote') + '" style="margin:0 0 10px"></div>' +
      '<div class="tscroll"><table class="tb" id="' + id('odds') + '"></table></div></div>';
  }
  function renderOdds(tab, d) {
    var pill = tab.$('oddspill'), note = tab.$('oddsnote'), tb = tab.$('odds');
    if (!pill || !tb) return;
    pill.textContent = d.rtp_pct + '% return · ' + d.house_edge_pct + '% edge';
    var bits = [d.rows + ' rows · ' + d.risk + ' risk · ' + (d.source === 'custom' ? 'your own table' : 'built-in table')];
    var pr = d.presets || {};
    ['low', 'medium', 'high'].forEach(function (k) { if (pr[k]) bits.push(k + ' ' + pr[k].rtp_pct + '%'); });
    note.className = 'rp-out';
    note.textContent = bits[0] + '   ·   presets for ' + d.rows + ' rows: ' + bits.slice(1).join(' · ') + (d.notes && d.notes.length ? '   ·   ' + d.notes.join('; ') : '');
    var rows = '<tr><th class="num">Slot</th><th>Pays</th><th class="num">Chance</th><th class="num opt">Odds</th><th class="num opt">Paths</th><th class="num">Return share</th></tr>';
    (d.slots || []).forEach(function (s) {
      var oneIn = s.probability > 0 ? (1 / s.probability) : 0;
      rows += '<tr><td class="num">' + (s.slot + 1) + '</td><td><b>' + (s.bust ? '☠ BUST' : esc(multLabel(s.mult))) + '</b></td><td class="num">' + pctLabel(s.pct) +
        '</td><td class="num opt">1 in ' + (oneIn >= 100 ? Math.round(oneIn).toLocaleString() : (Math.round(oneIn * 10) / 10)) + '</td><td class="num opt">' + s.ways + ' / ' + s.of.toLocaleString() +
        '</td><td class="num">' + (+s.rtp_pct).toFixed(2) + '%</td></tr>';
    });
    rows += '<tr><td colspan="5"><b>Return to player</b> <span class="note">(house edge ' + d.house_edge_pct + '%)</span></td><td class="num"><b>' + d.rtp_pct + '%</b></td></tr>';
    tb.innerHTML = rows;
  }

  // ======================================================================
  // the game
  // ======================================================================
  RC.register({
    key: 'hexfall', title: 'Hexfall',
    // the phase names in the state line, the editor's theme list
    phases: { idle: 'idle', betting: 'bets open', dropping: 'token falling', result: 'result', over: 'game over' },
    themes: [['coven', 'Coven — violet & green fire'], ['ember', 'Ember — hellfire'], ['frost', 'Frost — ice & silver']],
    // The only keys a /preview's `overrides` may carry — and what the Edit-Mode editor edits
    // (= hexfall.py APPEARANCE). `look` = their defaults (hexfall.py DEFAULTS): the editor's Reset.
    appearance: ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'show_odds', 'show_history', 'sfx', 'sfx_volume'],
    look: { x: 50, y: 50, scale: 0.85, theme: 'coven', title: 'Hexfall', show_rules: true, show_players: true,
      players_max: 8, show_odds: true, show_history: true, sfx: true, sfx_volume: 0.6 },
    base: [1280, 860],                                   // the renderer's BASE_W x BASE_H (if it is not loaded)
    // The game card's own text fields, in characters (code points, like hexfall.py - no maxlength, which
    // counts UTF-16 units: an emoji is 2): the player.
    fieldMax: { pu: 40 },
    // [key, label, kind, extra, title]
    settings: [
      ['drops', 'Drops per game', 'int', [1, 10]],
      ['rows', 'Rows of pegs', 'int', [8, 16], 'Rows of hexagonal pegs: rows + 1 slots at the bottom'],
      ['risk', 'Risk (built-in multiplier table)', 'select', [['low', 'Low — small swings'], ['medium', 'Medium'], ['high', 'High — the most busts, the biggest top payout']],
        'Which built-in table pays: the top payout sits in the centre, busts and small pays are interleaved among the better slots, about 95% return'],
      ['multipliers', 'Custom multipliers (rows + 1 numbers)', 'text', 400,
        'Your own table, left to right, e.g. 40 15 5 3 1 0.3 0 0.3 1 3 5 15 40 (0 = bust). Empty = the built-in table for rows and risk. It needs exactly rows + 1 numbers (a list that no longer fits is dropped); the return and house edge are computed from it'],
      ['open_bet_seconds', 'First bet window (s)', 'num', [5, 300]],
      ['between_seconds', 'Window before later drops (s)', 'num', [5, 300]],
      ['drop_seconds', 'Token fall (s)', 'num', [6, 20]],
      ['result_seconds', 'Result on screen (s)', 'num', [2, 30]],
      ['summary_seconds', 'Game-over card (s)', 'num', [4, 60]],
      ['min_bet', 'Min bet per drop', 'int', [1, 1e9], 'The smallest bet a player may put down on a drop (default 1)'],
      ['max_bet', 'Max bet per drop (0 = no max)', 'int', [0, 1e12], 'The most one player may have down on a drop, all their bets added together (default 250)'],
      ['currency', 'Currency', 'text', 24],
      ['commands_text', 'Commands line on the overlay', 'text', 120, 'Your bot\'s commands, e.g. !drop 100'],
      ['title', 'Title on the overlay', 'text', 32, 'Your branding: the name in the overlay\'s header (Edit Mode can preview it)'],
      ['hide_when_idle', 'Hide between games', 'bool'],
      ['sfx', 'Overlay sound effects', 'bool'],
      ['sfx_volume', 'Sound volume', 'num', [0, 1]],
      ['drop_clip', 'Soundboard clip: token released', 'clip'],
      ['win_clip', 'Soundboard clip: it pays the stake or more', 'clip'],
      ['bust_clip', 'Soundboard clip: it pays less', 'clip']
    ],
    reasons: { payout: 'payout' },                       // a ledger reason on top of the common ones
    textRows: {
      title: ['Title', 'Your branding: the name in the header']
    },
    hint: 'Live mirror of the overlay. A hex-themed Plinko: each drop chat bets an amount against the bank (your bot), then <b>one glowing skull</b> falls through rows of hexagonal pegs - the server flips a fair coin at every peg - into a slot that pays the bet <b>× its multiplier</b> (×0 is a bust). The slot odds, return and house edge are computed and shown; nothing here can steer the token.',
    scene: 'scene', ledgerNote: '', doc: 'hexfall',
    demoWord: 'drop',

    // ---- markup ----
    controls: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr 1fr;gap:9px;margin-top:14px">' +
        '<label class="f" title="Drops in this game (the settings\' number when empty)">Drops <input type="number" id="' + id('drops') + '" min="1" max="10" step="1" placeholder="settings"></label>' +
        '<label class="f" title="Rows of pegs for this game (the settings\' number when empty)">Rows <input type="number" id="' + id('rows') + '" min="8" max="16" step="1" placeholder="settings"></label>' +
        '<label class="f" title="Which built-in table pays this game">Risk <select id="' + id('risk') + '"><option value="">settings</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></label></div>' +
        '<div class="row"><button class="act" id="' + id('start') + '">Start game</button>' +
        '<button class="sec" id="' + id('test') + '" title="Plays exactly the same, but writes nothing to the ledger">Test game</button></div>';
    },
    play: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr;gap:9px">' +
        '<label class="f">User <input id="' + id('pu') + '" placeholder="viewer" autocomplete="off"></label>' +
        '<label class="f">Amount <input type="number" id="' + id('pa') + '" min="1" step="1" placeholder="coins"></label></div>' +
        '<div class="row"><button class="act" id="' + id('bet') + '">Bet on this drop</button>' +
        '<button class="sec" id="' + id('back') + '" title="Take back what went down in this betting window">Take back</button>' +
        '<button class="sec" id="' + id('cash') + '" style="display:none" tabindex="-1" aria-hidden="true">Cash out</button></div>';     // (the shared tab wires a cash-out button; Hexfall has none)
    },
    apiRows: function (a) {
      return [
        ['POST ' + a + '/start', '{drops, rows, risk, multipliers, seconds, test} — start a game (409 if one is running; 400 if multipliers does not have rows + 1 numbers)'],
        ['POST ' + a + '/bet', '{user, amount} — ledger debit; betting again adds to this drop\'s bet'],
        ['POST ' + a + '/remove', '{user} — take back this window\'s bet (refund)'],
        ['POST ' + a + '/next', 'end the current phase now (alias /drop)'],
        ['GET ' + a + '/table', 'the STATE: phase, drop, slots + odds, return, players, the falling token\'s path, last drop, summary'],
        ['GET ' + a + '/user/{name}', 'one player + their session totals'],
        ['GET ' + a + '/ledger?since=0', 'this game\'s ledger events (tail it by seq and pay them)'],
        ['POST ' + a + '/stop', 'end the game: a falling token counts (its slot pays), open bets refunded'],
        ['POST ' + a + '/preview', '{overrides, seconds} — Test in OBS (Edit Mode): on screen for a few seconds with that look; never touches the game (/preview/clear ends it)'],
        ['GET ' + a + '/history · /bets · /validate', 'finished games + stats · the slot table (multipliers, exact odds, return, house edge) + rules · dry-run a bet']
      ];
    },
    examples: function (h) {
      return [
        'curl -X POST ' + h + '/start -H "Content-Type: application/json" -d "{\\"rows\\":12,\\"risk\\":\\"high\\"}"',
        'curl "' + h + '/bet?user=alice&amount=100"',
        'curl "' + h + '/bet?user=carl&amount=50"',
        'curl "' + h + '/bets"',
        'curl "' + h + '/ledger?since=0"'].join('\n');
    },
    extra: oddsCard,

    // ---- wiring ----
    startBody: function (b, $$) {
      var d = parseInt($$('drops').value, 10), r = parseInt($$('rows').value, 10), k = $$('risk').value;
      if (isFinite(d)) b.drops = d;
      if (isFinite(r)) b.rows = r;
      if (k) b.risk = k;
    },
    wire: function (self, $$, who, pout) {
      if ($$('back')) $$('back').onclick = function () {
        self.act('/remove', { user: who() }, function (d) { var c = (d.credits || [])[0]; return c ? 'Took back ' + fmt(c.amount) : 'ok'; }, pout);
      };
    },
    onConfig: function (tab) {                           // the table follows the settings: read it again (debounced)
      var m = tab.$('s-multipliers');
      if (m) m.placeholder = 'empty = the built-in table';
      if (!tab.lbooted) return;
      clearTimeout(tab._oddsT);
      tab._oddsT = setTimeout(function () { tab.loadOdds(); }, 200);
    },
    onFirstTab: function (tab) { tab.loadOdds(); },
    afterSave: function (tab) { clearTimeout(tab._oddsT); tab._oddsT = setTimeout(function () { tab.loadOdds(); }, 400); },
    methods: {
      // a custom table must have rows + 1 numbers: say so here instead of letting the server drop it quietly
      saveSettings: function () {
        var rows = Math.round(num(this.$('s-rows') && this.$('s-rows').value, this.cfg.rows)), el = this.$('s-multipliers'), txt = el ? el.value.trim() : '';
        if (txt) {
          var parts = txt.split(/[\s,;]+/).filter(Boolean);
          if (parts.length !== rows + 1) { toast(rows + ' rows have ' + (rows + 1) + ' slots: the table needs ' + (rows + 1) + ' multipliers, not ' + parts.length, 4200); return; }
          var bad = parts.filter(function (p) { var v = parseFloat(p.replace(/^[x×*]/i, '')); return !(p.toLowerCase() === 'bust' || (isFinite(v) && v >= 0 && v <= 1000)); });
          if (bad.length) { toast('Not a multiplier (0 - 1000): ' + bad[0], 4200); return; }
        }
        return Object.getPrototypeOf(this).saveSettings.call(this);
      },
      loadOdds: async function () {
        var r = await this.req('GET', this.api + '/bets');
        if (r.ok) renderOdds(this, r.d);
      }
    },

    // ---- readouts ----
    sub: function (tab, g) {
      return 'drop ' + g.drop + ' of ' + g.drops + ' · ' + g.rows + ' rows · ' + g.risk + ' risk' + (g.source === 'custom' ? ' · custom table' : '') + (g.test ? ' · TEST GAME' : '');
    },
    facts: function (tab, g, cur) {
      var facts = [['Table', g.rows + ' rows · ' + esc(g.risk) + (g.source === 'custom' ? ' <span class="note">(custom)</span>' : '')],
        ['Return', g.rtp_pct + '% <span class="note">(house ' + g.house_edge_pct + '%)</span>'],
        ['On the line', fmt(g.on_the_line) + ' ' + esc(cur)], ['Drops played', (g.hits || []).length + ' of ' + g.drops]];
      if (g.last && (g.phase === 'result' || g.phase === 'over')) facts.push(['Last drop', g.last.bust ? '☠ BUST' : esc(multLabel(g.last.mult)) + ' <span class="note">(slot ' + (g.last.slot + 1) + ')</span>']);
      return facts;
    },
    idleFacts: function (idle) {
      return [['Table', (idle.rows || '') + ' rows · ' + esc(idle.risk || '') + (idle.source === 'custom' ? ' (custom)' : '')],
        ['Return', (idle.rtp_pct != null ? idle.rtp_pct + '%' : '—')], ['Drops', idle.drops]];
    },
    playerRows: function (ps) {
      return '<tr><th>User</th><th class="num">Bet on this drop</th><th class="num">Best case</th></tr>' +
        ps.map(function (p) { return '<tr><td>' + esc(p.user) + '</td><td class="num">' + fmt(p.bet) + '</td><td class="num">' + fmt(p.max_win) + '</td></tr>'; }).join('');
    },
    historyBits: function (s) {
      return [['Games', s.games], ['Complete', s.complete], ['Drops', s.drops], ['Busts', s.busts], ['Total bet', fmt(s.total_bet)], ['Paid', fmt(s.total_paid)], ['House net', signed(s.house_net)]];
    },

    // ---- the editor ----
    rulesExtra: function (P) {
      return '<label class="tog"><input type="checkbox" id="' + P + 'show_odds"> payout ladder</label>' +
        '<label class="tog"><input type="checkbox" id="' + P + 'show_history"> last hits strip</label>';
    },
    extraChecks: ['show_odds', 'show_history'],
    sample: hfSample, preview: hfPreview, demoTiming: hfDemoTiming
  });
})();
