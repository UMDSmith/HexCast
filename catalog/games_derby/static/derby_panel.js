/*
 * Hexcast Games — Hellhound Derby panel tab                          derby_panel.js
 *
 * The Hellhound Derby tab of the Games panel (section#tab-derby): hellhounds race on one track and chat bets on
 * the WINNER by its race number. The panel itself (OBS card, live mirror, controls, play as, players, history,
 * ledger, settings, placement & look editor, API card) is round_common.js, shared with the other round games;
 * this file is the game: its defaults, settings, wording, API rows and the form book card. The editor's sample
 * lineup and demo race come from the renderer (derby.js: HexGames.derby.sampleState / demoSteps).
 *
 * Plain browser script. games_panel.html loads it after round_common.js and the derby.js renderer
 * (window.HexGames.derby) when the Hellhound Derby is installed. It needs nothing of the other games.
 */
(function () {
  'use strict';

  var GP = window.GamesPage || {};
  var RC = GP.round;
  if (!RC) return;
  var esc = GP.esc, num = RC.num, clamp = RC.clamp, fmt = RC.fmt, signed = RC.signed, roundState = RC.roundState;
  function toast(m, ms) { if (typeof GP.toast === 'function') GP.toast(m, ms); }

  function R() { return (window.HexGames && window.HexGames.derby) || null; }

  // ======================================================================
  // the editor's sample lineup and demo race (built by the renderer: the same STATE the server sends)
  // ======================================================================
  function dbSample(c, gid, ms) {
    var r = R();
    if (r && r.sampleState) return r.sampleState(c, gid, ms);
    // the renderer is not loaded: an empty lineup (the editor shows its "renderer not loaded" note anyway)
    return roundState({ id: gid, test: false, phase: 'betting', ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0, field: [], edge_pct: 5,
      script: null, markers: [], players: [], at_risk: 0, last: null, outcome: null, summary: null, currency: 'coins',
      min_bet: 1, max_bet: 0, commands_text: '' }, { form: [] });
  }
  function dbPreview(c, gid) {
    var r = R();
    return r && r.demoSteps ? r.demoSteps(c, gid) : null;
  }
  // ▶ Preview's timings: the result card is shown for at most 5 s (the demo's own length, see demoSteps)
  function dbDemoTiming(c) {
    return { result_seconds: Math.min(clamp(num(c.result_seconds, 12), 3, 60), 5) };
  }

  // ======================================================================
  // the form book card (GET /form): every hound's record, which is what sets the odds
  // ======================================================================
  function formCard(id) {
    return '<div class="card"><h2>Form book <span class="pill" id="' + id('formpill') + '">—</span></h2>' +
      '<p class="hint">Every hound\'s record across your races. The odds of a new lineup come from it: a hound on a hot streak is the favourite and ' +
      'pays less, a cold one pays more (plus a little daily luck, <b>Luck</b> in Settings; <b>Form weight</b> sets how much form counts). ' +
      'The winner is drawn with exactly the chances shown on the lineup. Test games don\'t count.</p>' +
      '<div class="tscroll"><table class="tb" id="' + id('form') + '"></table></div>' +
      '<div class="row"><button class="sec" id="' + id('formclear') + '" title="Every hound back to no races: equal odds again">Clear the form book</button></div></div>';
  }
  function places(last) {
    return (last || []).map(function (p) { return p === 1 ? '<b>1</b>' : String(p); }).join(' ');
  }
  function renderForm(tab, rows) {
    var tb = tab.$('form'), pill = tab.$('formpill');
    if (!tb) return;
    var races = rows.reduce(function (a, r) { return Math.max(a, r.races); }, 0);
    pill.textContent = races ? 'busiest hound: ' + races + ' race' + (races === 1 ? '' : 's') : 'no races yet';
    tb.innerHTML = '<tr><th>Hound</th><th class="num">Races</th><th class="num">Wins</th><th>Last places <span class="note">(newest first)</span></th><th>Streak</th><th class="opt">Form</th></tr>' +
      rows.map(function (r) {
        var st = r.streak > 0 ? '🔥 won ' + r.streak + (r.streak === 1 ? '' : ' in a row') : r.streak < 0 ? (-r.streak) + ' without a win' : '—';
        return '<tr><td>' + esc(r.name) + '</td><td class="num">' + r.races + '</td><td class="num">' + r.wins + '</td><td>' + (places(r.last) || '<span class="note">—</span>') +
          '</td><td>' + esc(st) + '</td><td class="opt note">' + esc(r.note || '') + '</td></tr>';
      }).join('');
  }

  // ======================================================================
  // the game
  // ======================================================================
  RC.register({
    key: 'derby', title: 'Hellhound Derby',
    // the phase names in the state line, the editor's theme list
    phases: { idle: 'idle', betting: 'bets open (the lineup)', racing: 'the hounds are racing', result: 'result' },
    themes: [['inferno', 'Inferno — lava & hellfire'], ['abyss', 'Abyss — cold blue fire'], ['sulfur', 'Sulfur — yellow brimstone']],
    // The only keys a /preview's `overrides` may carry — and what the Edit-Mode editor edits
    // (= derby.py APPEARANCE). `look` = their defaults (derby.py DEFAULTS): the editor's Reset.
    appearance: ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'sfx', 'sfx_volume',
      'show_talk', 'clean_language', 'show_crowd'],
    look: { x: 50, y: 50, scale: 1, theme: 'inferno', title: 'Hellhound Derby', show_rules: true, show_players: true,
      players_max: 8, sfx: true, sfx_volume: 0.6, show_talk: true, clean_language: false, show_crowd: true },
    base: [1920, 1080],                                  // the renderer's BASE_W x BASE_H (if it is not loaded)
    // The game card's own text fields, in characters (code points, like derby.py): the player.
    fieldMax: { pu: 40 },
    // [key, label, kind, extra, title]
    settings: [
      ['field_size', 'Hounds per race', 'int', [3, 6], 'Drawn from the roster for every race; they wear the numbers 1 to this'],
      ['benched', 'Benched hounds', 'text', 200, 'Hounds that never race, comma separated (ids or names): cinder, gnash, terry, sparks, bubba, ember, brimstone, mange, harry. At least 3 must be left'],
      ['bet_seconds', 'Lineup / bet window (s)', 'num', [5, 300], 'The lineup with the odds board: chat bets while it shows'],
      ['race_seconds', 'Race length (s, about)', 'num', [25, 90], 'Roughly how long the winner takes; the rest of the field finishes after it'],
      ['result_seconds', 'Result card (s)', 'num', [3, 60]],
      ['run_without_bets', 'Race even when nobody bet', 'bool', null, 'Off: a lineup without bets ends quietly instead of racing'],
      ['house_edge_pct', 'House edge %', 'num', [0, 25], 'Every multiplier is (1 - edge) / the hound\'s chance, rounded down'],
      ['form_weight', 'Form weight', 'num', [0, 2], 'How much the form book moves the odds: 0 = every hound the same (only luck), 1 = normal, 2 = streaks count double'],
      ['luck', 'Daily luck', 'num', [0, 1], 'Random spread on each lineup\'s odds: 0 = form only, 0.35 = normal, 1 = wild'],
      ['min_bet', 'Min bet', 'int', [1, 1e9]],
      ['max_bet', 'Max per player per race (0 = none)', 'int', [0, 1e12], 'All of a player\'s bets on one race together'],
      ['max_payout', 'Max payout per bet (0 = none)', 'int', [0, 1e12], 'A winning bet pays at most this (never less than its stake)'],
      ['currency', 'Currency', 'text', 24],
      ['commands_text', 'Commands line on the overlay', 'text', 120, 'Your bot\'s bet command, shown on the lineup, e.g. !derby <number> <coins>'],
      ['title', 'Title on the overlay', 'text', 32, 'Your branding: the name in the overlay\'s header (Edit Mode can preview it)'],
      ['show_talk', 'Hounds talk trash', 'bool', null, 'Their speech bubbles (only when one passes another, and at events)'],
      ['clean_language', 'Clean language', 'bool', null, 'Their swearing becomes #@$%'],
      ['show_crowd', 'Crowd throws junk', 'bool', null, 'The background lobs (dressing). Junk that hits a hound is part of the race and always shows'],
      ['hide_when_idle', 'Hide between games', 'bool'],
      ['sfx', 'Overlay sound effects', 'bool'],
      ['sfx_volume', 'Sound volume', 'num', [0, 1]],
      ['start_clip', 'Soundboard clip: the gates open', 'clip'],
      ['finish_clip', 'Soundboard clip: the race is over', 'clip']
    ],
    reasons: {},                                         // (no ledger reasons beyond the common ones: bet, add, win, refund)
    textRows: {
      title: ['Title', 'Your branding: the name in the header']
    },
    hint: 'Live mirror of the overlay. Hellhounds race on one track and chat bets on the <b>winner</b> by its race number; a winning bet pays its multiplier, anything else loses the stake. Every bet is against the bank (your bot). Odds come from the hounds\' form, and the server draws the winner with exactly those chances (a cryptographic RNG) when the bets close: the tumbles, bites, flaming arses and thrown junk are only dressing.',
    scene: 'scene', ledgerNote: '; this game has no cash-outs - a lost stake is simply gone', doc: 'derby',
    demoWord: 'race',

    // ---- markup ----
    controls: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 2fr;gap:9px;margin-top:14px">' +
        '<label class="f" title="The lineup / bet window of this race (the settings\' seconds when empty)">Bet window (s) <input type="number" id="' + id('secs') + '" min="5" max="300" step="1" placeholder="settings"></label></div>' +
        '<div class="row"><button class="act" id="' + id('start') + '">Start race</button>' +
        '<button class="sec" id="' + id('test') + '" title="Plays exactly the same, but writes nothing to the ledger or the form book">Test race</button></div>';
    },
    play: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr 1fr;gap:9px">' +
        '<label class="f">User <input id="' + id('pu') + '" placeholder="viewer" autocomplete="off"></label>' +
        '<label class="f">Hound <input id="' + id('ph') + '" placeholder="number or name" autocomplete="off"></label>' +
        '<label class="f">Amount <input type="number" id="' + id('pa') + '" min="1" step="1" placeholder="coins"></label></div>' +
        '<div class="row"><button class="act" id="' + id('bet') + '">Bet</button>' +
        '<button class="sec" id="' + id('cash') + '" title="Take back this player\'s bets in this window (only the one hound, if you fill one in)">Take back</button></div>';
    },
    apiRows: function (a) {
      return [
        ['POST ' + a + '/start', '{seconds, test} — open a lineup (409 if a race is running); returns the field: number, hound, chance, multiplier'],
        ['POST ' + a + '/bet', '{user, amount, number} — ledger debit on the hound with that race number (its name works too); the same hound again adds'],
        ['POST ' + a + '/remove', '{user, number?} — take back what went down in this window (refund)'],
        ['POST ' + a + '/next', 'end the current phase now (close the bets, finish the race)'],
        ['GET ' + a + '/table', 'the STATE: phase, the field with odds, money on each hound, players, the race script, result, summary'],
        ['GET ' + a + '/user/{name}', 'one player + their session totals'],
        ['GET ' + a + '/ledger?since=0', 'this game\'s ledger events (tail it by seq and pay them)'],
        ['GET ' + a + '/bets', 'the rules, the odds model, the current field and the form book'],
        ['GET ' + a + '/validate?number=3&amount=100', 'dry-run a bet: hound, chance, multiplier, payout'],
        ['GET ' + a + '/form · POST /form/clear', 'the form book (races, wins, last places, streak) · reset it'],
        ['POST ' + a + '/stop', 'end the game: a race already drawn counts, an open window is refunded'],
        ['POST ' + a + '/preview', '{overrides, seconds} — Test in OBS (Edit Mode): on screen for a few seconds with that look; never touches the game (/preview/clear ends it)'],
        ['GET ' + a + '/history · /last', 'finished races + stats · the last one']
      ];
    },
    examples: function (h) {
      return [
        'curl -X POST ' + h + '/start -H "Content-Type: application/json" -d "{\\"seconds\\":22}"',
        'curl "' + h + '/bet?user=alice&number=3&amount=100"',
        'curl "' + h + '/bet?user=bob&number=5&amount=25"',
        'curl "' + h + '/validate?number=3&amount=100"',
        'curl "' + h + '/bets"',
        'curl "' + h + '/ledger?since=0"'].join('\n');
    },
    extra: formCard,

    // ---- wiring ----
    startBody: function (b, $$) {
      var s = parseFloat($$('secs').value);
      if (isFinite(s)) b.seconds = s;
    },
    betBody: function (b, $$) { b.number = $$('ph').value.trim(); },
    wire: function (self, $$, who, pout) {
      // "Cash out" of the shared form is "Take back" here: only a bet whose window is still open can be returned
      $$('cash').onclick = function () {
        var body = { user: who() }, h = $$('ph').value.trim();
        if (h) body.number = h;
        self.act('/remove', body, function (d) {
          var total = (d.credits || []).reduce(function (a, c) { return a + c.amount; }, 0);
          return total ? 'Took back ' + fmt(total) : 'ok';
        }, pout);
      };
      $$('formclear').onclick = async function () {
        if (!confirm('Clear the form book? Every hound goes back to no races, so the odds start even again.')) return;
        var r = await self.req('POST', self.api + '/form/clear');
        if (r.ok) { renderForm(self, r.d.form || []); toast('Form book cleared'); }
        else toast(r.d.error || 'Could not clear the form book');
      };
    },
    onFirstTab: function (tab) { tab.loadForm(); },
    methods: {
      loadForm: async function () {
        var r = await this.req('GET', this.api + '/form');
        if (r.ok) renderForm(this, r.d.form || []);
      },
      // the form book changes when a race ends: read it again then (and once the tab is first opened)
      onState: function (st) {
        Object.getPrototypeOf(this).onState.call(this, st);
        var g = st && st.game, key = g && g.phase === 'result' ? g.id : null;
        if (key && key !== this._formKey && this.lbooted) { this._formKey = key; this.loadForm(); }
      }
    },

    // ---- readouts ----
    sub: function (tab, g) {
      return (g.field || []).length + ' hounds' + (g.test ? ' · TEST RACE' : '');
    },
    facts: function (tab, g, cur) {
      var field = g.field || [], fav = field.reduce(function (a, b) { return !a || b.chance > a.chance ? b : a; }, null);
      var facts = [['Favourite', fav ? '#' + fav.num + ' ' + esc(fav.name) + ' <span class="note">×' + (+fav.mult).toFixed(2) + '</span>' : '—'],
        ['On the line', fmt(g.at_risk) + ' ' + esc(cur)], ['Backed', (g.markers || []).filter(function (m) { return m.total > 0; }).length + ' of ' + field.length + ' hounds']];
      if (g.last && g.phase === 'result') {
        var w = g.last.winner, p = g.last.photo;
        facts.push(['Winner', w ? '#' + w.num + ' ' + esc(w.name) + (p && p.kind === 'win' ? ' <span class="note">by ' + esc(String(p.label).toLowerCase()) + '</span>' : '') : '—']);
      }
      return facts;
    },
    idleFacts: function (idle) {
      var form = idle.form || [], top = form[0];
      return [['Hounds per race', idle.field_size], ['Form leader', top && top.wins ? esc(top.name) + ' <span class="note">(' + top.wins + ' wins)</span>' : '—']];
    },
    playerRows: function (ps) {
      return '<tr><th>User</th><th>Bets (hound · multiplier → pays)</th><th class="num">Stake</th></tr>' +
        ps.map(function (p) {
          var bets = p.bets.map(function (b) { return '#' + b.num + ' ' + fmt(b.amount) + ' ×' + (+b.mult).toFixed(2) + ' → ' + fmt(b.pays); }).join(' · ');
          return '<tr><td>' + esc(p.user) + '</td><td>' + esc(bets) + '</td><td class="num">' + fmt(p.stake) + '</td></tr>';
        }).join('');
    },
    historyBits: function (s) {
      return [['Games', s.games], ['Races', s.races], ['Photo finishes', s.photo_finishes], ['Favourite won', s.favourite_wins], ['Longshot won', s.longshot_wins],
        ['Total bet', fmt(s.total_bet)], ['Paid', fmt(s.total_paid)], ['House net', signed(s.house_net)]];
    },

    // ---- the editor ----
    rulesExtra: function (P) {
      return '<label class="tog"><input type="checkbox" id="' + P + 'show_talk"> trash talk</label>' +
        '<label class="tog"><input type="checkbox" id="' + P + 'clean_language"> clean language</label>' +
        '<label class="tog"><input type="checkbox" id="' + P + 'show_crowd"> crowd throws junk</label>';
    },
    extraChecks: ['show_talk', 'clean_language', 'show_crowd'],
    sample: dbSample, preview: dbPreview, demoTiming: dbDemoTiming
  });
})();
