/*
 * Hexcast Games — Soul Climb panel tab                              climb_panel.js
 *
 * The Soul Climb tab of the Games panel (section#tab-climb): a damned soul climbs out of a hell pit and chat bets
 * on HOW HIGH he gets. The panel itself (OBS card, live mirror, controls, play as, players, history, ledger,
 * settings, placement & look editor, API card) is round_common.js, shared with Russian Roulette and Trivia; this
 * file is the game: its defaults, settings, wording and API rows. The editor's sample game and demo climb come from
 * the renderer (climb.js: HexGames.climb.sampleState / demoSteps).
 *
 * Plain browser script. games_panel.html loads it after round_common.js and the climb.js renderer
 * (window.HexGames.climb) when Soul Climb is installed. It needs nothing of the other games.
 */
(function () {
  'use strict';

  var GP = window.GamesPage || {};
  var RC = GP.round;
  if (!RC) return;
  var esc = GP.esc, num = RC.num, clamp = RC.clamp, fmt = RC.fmt, signed = RC.signed, roundState = RC.roundState;

  function R() { return (window.HexGames && window.HexGames.climb) || null; }

  // ======================================================================
  // the editor's sample game and demo climb (built by the renderer: the same STATE the server sends)
  // ======================================================================
  function climbSample(c, gid, ms) {
    var r = R();
    if (r && r.sampleState) return r.sampleState(c, gid, ms);
    // the renderer is not loaded: an empty bet window (the editor shows its "renderer not loaded" note anyway)
    return roundState({ id: gid, test: false, phase: 'betting', ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0, climb: 1, climbs: 1, height: 100,
      soul: { name: 'Gary', seed: 1, skin: 0, gear: 0 }, edge_pct: 5, escape_pct: 2, script: null, markers: [], players: [], at_risk: 0, odds: [],
      last: null, results: [], outcome: null, summary: null, currency: 'coins', min_bet: 1, max_bet: 0, max_bets: 5, commands_text: '' }, {});
  }
  function climbPreview(c, gid) {
    var r = R();
    return r && r.demoSteps ? r.demoSteps(c, gid) : null;
  }
  // ▶ Preview's timings: the result card is shown for at most 4 s (the demo's own length, see demoSteps)
  function climbDemoTiming(c) {
    return { result_seconds: Math.min(clamp(num(c.result_seconds, 8), 2, 30), 4) };
  }

  // ======================================================================
  // the game
  // ======================================================================
  RC.register({
    key: 'climb', title: 'Soul Climb',
    // the phase names in the state line, the editor's theme list
    phases: { idle: 'idle', betting: 'bets open', climbing: 'the soul is climbing', result: 'result', over: 'game over' },
    themes: [['inferno', 'Inferno — lava & embers'], ['abyss', 'Abyss — cold blue fire'], ['sulfur', 'Sulfur — yellow brimstone']],
    // The only keys a /preview's `overrides` may carry — and what the Edit-Mode editor edits
    // (= climb.py APPEARANCE). `look` = their defaults (climb.py DEFAULTS): the editor's Reset.
    appearance: ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'show_odds', 'sfx', 'sfx_volume'],
    look: { x: 50, y: 50, scale: 1, theme: 'inferno', title: 'Soul Climb', show_rules: true, show_players: true,
      players_max: 8, show_odds: true, sfx: true, sfx_volume: 0.6 },
    base: [880, 960],                                    // the renderer's BASE_W x BASE_H (if it is not loaded)
    // The game card's own text fields, in characters (code points, like climb.py - no maxlength, which counts
    // UTF-16 units: an emoji is 2): the soul's name(s) (SOUL_NAME_MAX x a few), the player.
    fieldMax: { soul: 120, pu: 40 },
    // [key, label, kind, extra, title]
    settings: [
      ['climbs', 'Climbs per game', 'int', [1, 3], 'Each climb has its own betting window'],
      ['max_height', 'Pit height (levels)', 'int', [10, 300], 'Bets are heights 1 to this; the top of the pit is the escape (the jackpot)'],
      ['escape_pct', 'Escape chance %', 'num', [0.1, 50], 'The chance a soul makes it all the way out. Sets how steep the pit is: every level has a fall chance that rises with height'],
      ['house_edge_pct', 'House edge %', 'num', [0, 25]],
      ['open_bet_seconds', 'First bet window (s)', 'num', [5, 300]],
      ['between_seconds', 'Window before later climbs (s)', 'num', [5, 300]],
      ['climb_speed', 'Climb speed (×)', 'num', [0.5, 2], '1 = normal, 0.5 = twice as slow, 2 = twice as fast (the whole show)'],
      ['result_seconds', 'Result card (s)', 'num', [2, 30]],
      ['summary_seconds', 'Game-over card (s)', 'num', [4, 60]],
      ['min_bet', 'Min bet', 'int', [1, 1e9]],
      ['max_bet', 'Max per player per climb (0 = none)', 'int', [0, 1e12], 'All of a player\'s bets on one climb together'],
      ['max_payout', 'Max payout per bet (0 = none)', 'int', [0, 1e12], 'A winning bet pays at most this (never less than its stake)'],
      ['soul_name', 'Soul name(s)', 'text', 120, 'Comma separated: one is picked for every climb. Empty = the built-in names (Gary, Brenda, Kevin ...)'],
      ['currency', 'Currency', 'text', 24],
      ['commands_text', 'Commands line on the overlay', 'text', 120, 'Your bot\'s commands, e.g. !climb 100 40'],
      ['title', 'Title on the overlay', 'text', 32, 'Your branding: the name in the overlay\'s header (Edit Mode can preview it)'],
      ['hide_when_idle', 'Hide between games', 'bool'],
      ['sfx', 'Overlay sound effects', 'bool'],
      ['sfx_volume', 'Sound volume', 'num', [0, 1]],
      ['climb_clip', 'Soundboard clip: the climb starts', 'clip'],
      ['fall_clip', 'Soundboard clip: he falls', 'clip'],
      ['escape_clip', 'Soundboard clip: he escapes', 'clip']
    ],
    reasons: {},                                         // (no ledger reasons beyond the common ones: bet, add, win, refund)
    textRows: {
      title: ['Title', 'Your branding: the name in the header']
    },
    hint: 'Live mirror of the overlay. A damned soul climbs out of a hell pit; chat bets on <b>how high</b> he gets (a height from 1 to the top). Reach it and the bet pays its multiplier, fall first and the stake is lost; the top of the pit is the jackpot. Every bet is against the bank (your bot). The server rolls every climb with a cryptographic RNG — the demons, bats and funny falls are only dressing.',
    scene: 'scene', ledgerNote: '; this game has no cash-outs - a lost stake is simply gone', doc: 'climb',
    demoWord: 'climb',

    // ---- markup ----
    controls: function (id) {
      return '<div class="grid" style="grid-template-columns:2fr 1fr;gap:9px;margin-top:14px">' +
        '<label class="f">Soul name(s) <input id="' + id('soul') + '" placeholder="empty = built-in names" autocomplete="off"></label>' +
        '<label class="f">Climbs <select id="' + id('climbs') + '"><option value="">(settings)</option><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></label></div>' +
        '<div class="row"><button class="act" id="' + id('start') + '">Start game</button>' +
        '<button class="sec" id="' + id('test') + '" title="Plays exactly the same, but writes nothing to the ledger">Test game</button></div>';
    },
    play: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr 1fr;gap:9px">' +
        '<label class="f">User <input id="' + id('pu') + '" placeholder="viewer" autocomplete="off"></label>' +
        '<label class="f">Height <input type="number" id="' + id('ph') + '" min="1" step="1" placeholder="level"></label>' +
        '<label class="f">Amount <input type="number" id="' + id('pa') + '" min="1" step="1" placeholder="coins"></label></div>' +
        '<div class="row"><button class="act" id="' + id('bet') + '">Bet</button>' +
        '<button class="sec" id="' + id('cash') + '" title="Take back this player\'s bets in this window (a height, if you fill one in)">Take back</button></div>';
    },
    apiRows: function (a) {
      return [
        ['POST ' + a + '/start', '{soul, climbs, seconds, test} — start a game (409 if one is running)'],
        ['POST ' + a + '/bet', '{user, amount, height} — ledger debit; up to 5 bets per player per climb (the same height again adds)'],
        ['POST ' + a + '/remove', '{user, height?} — take back what went down in this window (refund)'],
        ['POST ' + a + '/next', 'end the current phase now (close the bets, finish the climb)'],
        ['GET ' + a + '/table', 'the STATE: phase, climb, flags (markers), players, script, result, summary'],
        ['GET ' + a + '/user/{name}', 'one player + their session totals'],
        ['GET ' + a + '/ledger?since=0', 'this game\'s ledger events (tail it by seq and pay them)'],
        ['GET ' + a + '/bets', 'the rules + the table of every height: chance and multiplier'],
        ['GET ' + a + '/validate?height=40&amount=100', 'dry-run a bet: multiplier, chance, payout'],
        ['POST ' + a + '/stop', 'end the game: a climb already decided counts, an open window is refunded'],
        ['POST ' + a + '/preview', '{overrides, seconds} — Test in OBS (Edit Mode): on screen for a few seconds with that look; never touches the game (/preview/clear ends it)'],
        ['GET ' + a + '/history · /last', 'finished games + stats · the last one']
      ];
    },
    examples: function (h) {
      return [
        'curl -X POST ' + h + '/start -H "Content-Type: application/json" -d "{\\"soul\\":\\"Gary\\",\\"climbs\\":1}"',
        'curl "' + h + '/bet?user=alice&height=40&amount=100"',
        'curl "' + h + '/bet?user=bob&height=70&amount=25"',
        'curl "' + h + '/validate?height=40&amount=100"',
        'curl "' + h + '/bets"',
        'curl "' + h + '/ledger?since=0"'].join('\n');
    },

    // ---- wiring ----
    startBody: function (b, $$, field) {
      var sn = field('soul'), n = $$('climbs').value;
      if (sn) b.soul = sn;
      if (n) b.climbs = +n;
    },
    betBody: function (b, $$) { b.height = $$('ph').value; },
    wire: function (self, $$, who, pout, field) {
      // "Cash out" of the shared form is "Take back" here: only a bet that has not been climbed yet can be returned
      $$('cash').onclick = function () {
        var body = { user: who() }, h = $$('ph').value;
        if (h) body.height = h;
        self.act('/remove', body, function (d) {
          var total = (d.credits || []).reduce(function (a, c) { return a + c.amount; }, 0);
          return total ? 'Took back ' + fmt(total) : 'ok';
        }, pout);
      };
    },

    // ---- readouts ----
    sub: function (tab, g) {
      return 'climb ' + g.climb + ' of ' + g.climbs + ' · ' + g.soul.name + (g.test ? ' · TEST GAME' : '');
    },
    facts: function (tab, g, cur) {
      var facts = [['Soul', esc(g.soul.name)], ['Pit', g.height + ' levels · escape ' + (+g.escape_pct) + '%'],
        ['On the line', fmt(g.at_risk) + ' ' + esc(cur)], ['Flags', (g.markers || []).length + ' height' + ((g.markers || []).length === 1 ? '' : 's')]];
      if (g.last && (g.phase === 'result' || g.phase === 'over')) facts.push(['Last climb', g.last.escaped ? '⛰ ESCAPED' : 'reached ' + g.last.max]);
      return facts;
    },
    idleFacts: function (idle) {
      return [['Next soul', esc((idle.soul || {}).name || '')], ['Pit', idle.height + ' levels'], ['Climbs', idle.climbs]];
    },
    playerRows: function (ps) {
      return '<tr><th>User</th><th>Bets (height · multiplier → pays)</th><th class="num">Stake</th></tr>' +
        ps.map(function (p) {
          var bets = p.bets.map(function (b) { return 'L' + b.height + ' ×' + (+b.mult).toFixed(2) + ' → ' + fmt(b.pays); }).join(' · ');
          return '<tr><td>' + esc(p.user) + '</td><td>' + esc(bets) + '</td><td class="num">' + fmt(p.stake) + '</td></tr>';
        }).join('');
    },
    historyBits: function (s) {
      var avg = s.climbs ? Math.round(s.total_height / s.climbs) : 0;
      return [['Games', s.games], ['Climbs', s.climbs], ['Escapes', s.escapes], ['Falls', s.falls], ['Best height', s.best_height], ['Avg height', avg],
        ['Total bet', fmt(s.total_bet)], ['Paid', fmt(s.total_paid)], ['House net', signed(s.house_net)]];
    },

    // ---- the editor ----
    rulesExtra: function (P) {
      return '<label class="tog"><input type="checkbox" id="' + P + 'show_odds"> payout ladder</label>';
    },
    extraChecks: ['show_odds'],
    sample: climbSample, preview: climbPreview, demoTiming: climbDemoTiming
  });
})();
