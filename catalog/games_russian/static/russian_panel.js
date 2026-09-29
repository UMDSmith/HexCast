/*
 * Hexcast Games — Russian Roulette panel tab                     russian_panel.js
 *
 * The Russian Roulette tab of the Games panel (section#tab-russian): a revolver and a stuffed dummy,
 * pull k loads k bullets. The panel itself (OBS card, live mirror, controls, play as, players, history,
 * ledger, settings, placement & look editor, API card) is round_common.js, shared with Trivia; this file
 * is the game: its defaults, settings, sample game for the editor, wording and API rows.
 *
 * Plain browser script. games_panel.html loads it after round_common.js and the russian.js renderer
 * (window.HexGames.russian) when Russian Roulette is installed. It needs nothing of Trivia.
 */
(function () {
  'use strict';

  var GP = window.GamesPage || {};
  var RC = GP.round;
  if (!RC) return;
  var esc = GP.esc, num = RC.num, clamp = RC.clamp, fmt = RC.fmt, signed = RC.signed, sum = RC.sum, roundState = RC.roundState;

  // ======================================================================
  // the editor's sample game and demo pull
  // ======================================================================
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

  // ======================================================================
  // the game
  // ======================================================================
  RC.register({
    key: 'russian', title: 'Russian Roulette',
    // the phase names in the state line, the editor's theme list
    phases: { idle: 'idle', betting: 'bets open', pulling: 'pulling the trigger', result: 'result', over: 'game over' },
    themes: [['saloon', 'Saloon — wood & brass'], ['noir', 'Noir — black & white'], ['neon', 'Neon']],
    // The only keys a /preview's `overrides` may carry — and what the Edit-Mode editor edits
    // (= games.py APPEARANCE). `look` = their defaults (games.py DEFAULTS): the editor's Reset.
    appearance: ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'show_odds', 'sfx', 'sfx_volume'],
    look: { x: 50, y: 50, scale: 1, theme: 'saloon', title: 'Russian Roulette', show_rules: true, show_players: true,
      players_max: 8, show_odds: true, sfx: true, sfx_volume: 0.6 },
    base: [1100, 560],                                   // the renderer's BASE_W x BASE_H (if it is not loaded)
    // The game card's own text fields, in characters (code points, like games.py - no maxlength, which
    // counts UTF-16 units: an emoji is 2): the dummy's name (DUMMY_NAME_MAX), the volunteer, the player.
    fieldMax: { dummy: 24, duser: 40, pu: 40 },
    // [key, label, kind, extra, title]
    settings: [
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
    reasons: { volunteer_cut: 'volunteer cut' },         // a ledger reason on top of the common ones
    textRows: {
      title: ['Title', 'Your branding: the name in the header']
    },
    hint: 'Live mirror of the overlay. A revolver and a stuffed dummy: pull k loads k bullets and re-spins; chat bets the dummy <b>survives</b> (rides and grows) or goes <b>bang</b> this pull. Every bet is against the bank (your bot). The server spins with a cryptographic RNG — nothing here can steer it.',
    scene: 'scene', ledgerNote: ', the volunteer\'s cut', doc: 'russian_roulette',
    demoWord: 'pull',

    // ---- markup ----
    controls: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr;gap:9px;margin-top:14px">' +
        '<label class="f">Dummy name <input id="' + id('dummy') + '" placeholder="Dummy" autocomplete="off"></label>' +
        '<label class="f">Volunteer (gets the cut) <input id="' + id('duser') + '" placeholder="chatter name" autocomplete="off"></label></div>' +
        '<div class="row"><button class="act" id="' + id('start') + '">Start game</button>' +
        '<button class="sec" id="' + id('test') + '" title="Plays exactly the same, but writes nothing to the ledger">Test game</button>' +
        '<button class="sec" id="' + id('setdummy') + '" title="Change the dummy (before the first pull, or for the next game)">Set dummy</button></div>';
    },
    play: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr 1fr;gap:9px">' +
        '<label class="f">User <input id="' + id('pu') + '" placeholder="viewer" autocomplete="off"></label>' +
        '<label class="f">Side <select id="' + id('ps') + '"><option value="survive">survive (it clicks)</option><option value="bang">bang (it fires)</option></select></label>' +
        '<label class="f">Amount <input type="number" id="' + id('pa') + '" min="1" step="1" placeholder="coins"></label></div>' +
        '<div class="row"><button class="act" id="' + id('bet') + '">Bet</button><button class="sec" id="' + id('cash') + '">Cash out</button>' +
        '<button class="sec" id="' + id('back') + '" title="Take back what went down in this betting window">Take back</button></div>';
    },
    apiRows: function (a) {
      return [
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
    },
    examples: function (h) {
      return [
        'curl -X POST ' + h + '/start -H "Content-Type: application/json" -d "{\\"dummy\\":\\"Bob\\",\\"dummy_user\\":\\"bob\\"}"',
        'curl "' + h + '/bet?user=alice&side=survive&amount=100"',
        'curl "' + h + '/bet?user=carl&side=bang&amount=50"',
        'curl "' + h + '/cashout?user=alice"',
        'curl "' + h + '/ledger?since=0"'].join('\n');
    },

    // ---- wiring ----
    startBody: function (b, $$, field) {
      var dn = field('dummy'), du = $$('duser').value.trim();
      if (dn) b.dummy = dn;
      if (du) b.dummy_user = du;
    },
    betBody: function (b, $$) { b.side = $$('ps').value; },
    wire: function (self, $$, who, pout, field) {
      if ($$('setdummy')) $$('setdummy').onclick = function () {
        self.act('/dummy', { dummy: field('dummy'), dummy_user: $$('duser').value.trim() }, function (d) { return 'Dummy set for ' + d.applies; });
      };
      if ($$('back')) $$('back').onclick = function () {
        self.act('/remove', { user: who() }, function (d) { var c = (d.credits || [])[0]; return c ? 'Took back ' + fmt(c.amount) : 'ok'; }, pout);
      };
    },

    // ---- readouts ----
    sub: function (tab, g) {
      return 'pull ' + g.round + ' of ' + g.rounds + ' · ' + g.round + ' bullet' + (g.round > 1 ? 's' : '') + (g.test ? ' · TEST GAME' : '');
    },
    facts: function (tab, g, cur) {
      var facts;
      facts = [['Dummy', esc(g.dummy.name) + (g.dummy.user ? ' <span class="note">(@' + esc(g.dummy.user) + ')</span>' : '')],
        ['Survived', g.survived + ' of ' + g.rounds], ['Survive stakes', fmt(g.at_risk) + ' ' + esc(cur)], ['Bang bets', fmt(g.bang_total) + ' ' + esc(cur)]];
      if (g.pull && (g.phase === 'result' || g.phase === 'over')) facts.push(['Last pull', g.pull.fired ? '💥 BANG' : 'click']);
      return facts;
    },
    idleFacts: function (idle) {
      return [['Next dummy', esc((idle.dummy || {}).name || '')], ['Pulls', idle.rounds]];
    },
    playerRows: function (ps) {
      return '<tr><th>User</th><th class="num">Survive stake</th><th class="num">Worth now</th><th class="num">If it clicks</th><th class="num">Bang</th></tr>' +
        ps.map(function (p) { return '<tr><td>' + esc(p.user) + '</td><td class="num">' + fmt(p.stake) + '</td><td class="num">' + fmt(p.value) + '</td><td class="num">' + (p.if_survives != null ? fmt(p.if_survives) : '—') + '</td><td class="num">' + (p.bang ? fmt(p.bang) + ' → ' + fmt(p.bang_pays) : '—') + '</td></tr>'; }).join('');;
    },
    historyBits: function (s) {
      return [['Games', s.games], ['Bangs', s.bangs], ['Survived', s.survived], ['Walked', s.walked], ['Total bet', fmt(s.total_bet)], ['Paid', fmt(s.total_paid)], ['House net', signed(s.house_net)]];
    },

    // ---- the editor ----
    rulesExtra: function (P) {
      return '<label class="tog"><input type="checkbox" id="' + P + 'show_odds"> payout ladder</label>';
    },
    extraChecks: ['show_odds'],
    sample: rrSample, preview: rrPreview, demoTiming: rrDemoTiming
  });
})();
