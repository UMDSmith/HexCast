/*
 * Hexcast Games — Blackjack panel tab                           blackjack_panel.js
 *
 * The Blackjack tab of the Games panel (section#tab-blackjack): Hex deals a Vegas shoe game to a table of
 * chatters. The panel itself (OBS card, live mirror, controls, play as, players, history, ledger, settings,
 * the placement & look editor, the API card) is round_common.js, shared with the other round games; this
 * file is the game: its settings, the seats card, the sample table and demo hand for the editor, the
 * wording and the API rows.
 *
 * Plain browser script. games_panel.html loads it after round_common.js and the blackjack.js renderer
 * (window.HexGames.blackjack) when Blackjack is installed. It needs nothing of the other games.
 */
(function () {
  'use strict';

  var GP = window.GamesPage || {};
  var RC = GP.round;
  if (!RC) return;
  var esc = GP.esc, fmt = RC.fmt, signed = RC.signed;

  function renderer() { return window.HexGames && window.HexGames.blackjack; }

  // ---- the seats card: every seat of the table at a glance (what the overlay shows, as text)
  var CSS = [
    '#tab-blackjack .bj-seats{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:8px}',
    '#tab-blackjack .bj-seat{background:#0c0c11;border:1px solid var(--line);border-radius:10px;padding:8px 10px;min-height:62px;position:relative}',
    '#tab-blackjack .bj-seat .n{position:absolute;top:7px;right:9px;font:800 11px var(--mono);color:var(--dim)}',
    '#tab-blackjack .bj-seat b{display:block;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:22px}',
    '#tab-blackjack .bj-seat .s{font:12px var(--mono);color:var(--dim);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '#tab-blackjack .bj-seat .a{font:700 12px var(--mono);color:var(--gold)}',
    '#tab-blackjack .bj-seat.empty{opacity:.45}',
    '#tab-blackjack .bj-seat.empty b{color:var(--dim);font-weight:600}',
    '#tab-blackjack .bj-seat.held{border-style:dashed}',
    '#tab-blackjack .bj-seat.turn{border-color:var(--gold)}',
    '#tab-blackjack .bj-seat.win{border-color:#1d4d33;background:#0e2118}',
    '#tab-blackjack .bj-seat.lose{border-color:#5a2a26}',
    '#tab-blackjack .bj-act{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}',
    '#tab-blackjack .bj-act button{padding:7px 11px;font-size:12.5px}',
    '#tab-blackjack .bj-q{margin-top:10px}'
  ].join('\n');

  function seatTiles(g) {
    var out = [], free = 0;
    (g.seats || []).forEach(function (s) {
      if (!s.user) { free++; out.push('<div class="bj-seat empty"><span class="n">' + s.n + '</span><b>open seat</b><div class="s">&nbsp;</div></div>'); return; }
      var cls = 'bj-seat', line = '', amt = '';
      if (s.state === 'held') { cls += ' held'; line = 'ante up to keep the seat' + (s.last_bet ? ' (' + fmt(s.last_bet) + ')' : ''); }
      else if (s.state === 'bet') { amt = fmt(s.bet); line = 'ready'; }
      else {
        var hs = s.hands || [];
        line = hs.map(function (h) {
          var t = h.total + (h.soft && h.total < 21 ? 's' : '');
          var w = h.result ? ' ' + h.result.outcome : (h.status !== 'active' && h.status !== 'stand' ? ' ' + h.status : '') + (h.act ? ' →' + h.act : '');
          return t + w;
        }).join(' | ');
        amt = fmt(s.stake);
        if (g.phase === 'action' && hs.some(function (h) { return h.status === 'active' && !h.act; })) cls += ' turn';
        var net = 0, any = false;
        hs.forEach(function (h) { if (h.result) { net += h.result.net; any = true; } });
        if (any) cls += net > 0 ? ' win' : net < 0 ? ' lose' : '';
      }
      if (s.leaving) line += ' · leaving';
      out.push('<div class="' + cls + '"><span class="n">' + s.n + '</span><b>' + esc(s.user) + '</b><div class="s">' + esc(line) + '</div>' + (amt ? '<div class="a">' + amt + ' on the line</div>' : '') + '</div>');
    });
    return { html: out.join(''), free: free };
  }

  function fillSeats(tab) {
    var g = tab.st && tab.st.game, box = tab.$('seatgrid'), q = tab.$('queue'), pill = tab.$('seatpill');
    if (!box) return;
    if (!g) {
      var n = (tab.st && tab.st.idle && tab.st.idle.seats) || tab.cfg.seats || 10;
      box.innerHTML = '<div class="note">No table open. Start one above — ' + n + ' seats around the felt.</div>';
      if (q) q.innerHTML = '';
      if (pill) pill.textContent = 'closed';
      return;
    }
    var t = seatTiles(g);
    box.innerHTML = t.html;
    if (pill) pill.textContent = (g.seats.length - t.free) + '/' + g.seats.length + ' seated';
    if (q) {
      q.innerHTML = g.queue && g.queue.length
        ? '<b>Waiting</b> (' + g.queue.length + '): ' + g.queue.map(function (e, i) { return (i + 1) + '. ' + esc(e.user) + ' <span class="note">(' + fmt(e.bet) + ')</span>'; }).join(' · ')
        : '<span class="note">Nobody is waiting for a seat.</span>';
    }
  }

  // ======================================================================
  // the game
  // ======================================================================
  RC.register({
    key: 'blackjack', title: 'Blackjack',
    phases: { idle: 'idle', betting: 'bets open', dealing: 'dealing', insurance: 'insurance', action: 'players acting', resolve: 'drawing cards',
      dealer: 'dealer plays', settle: 'paying out', over: 'table closed' },
    themes: [['classic', 'Classic — green felt'], ['neon', 'Neon'], ['midnight', 'Midnight — blue felt'], ['royal', 'Royal — burgundy & gold']],
    // The only keys a /preview's `overrides` may carry — and what the Edit-Mode editor edits (= blackjack.py APPEARANCE).
    appearance: ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'show_shoe', 'show_captions', 'sfx', 'sfx_volume'],
    look: { x: 50, y: 50, scale: 1, theme: 'classic', title: 'Blackjack', show_rules: true, show_players: true, players_max: 8, show_shoe: true,
      show_captions: true, sfx: true, sfx_volume: 0.6 },
    base: [1920, 1080],
    fieldMax: { pu: 40 },
    // [key, label, kind, extra, title]
    settings: [
      ['seats', 'Seats (1–14)', 'int', [1, 14], 'Seats around the felt. Everybody else waits in the queue'],
      ['seat_fill', 'Seat order', 'select', [['center', 'From the middle out'], ['first', 'Seat 1, 2, 3 …']], 'Which free seat the next player gets'],
      ['queue_max', 'Waiting list size', 'int', [0, 50]],
      ['min_bet', 'Min bet', 'int', [1, 1e9]],
      ['max_bet', 'Max bet (0 = none)', 'int', [0, 1e12], 'The ante, per seat'],
      ['deck_table', 'Decks by players', 'text', 40, 'The shoe grows with the table: "from N players: M decks", e.g. 1:2,3:4,5:6,8:8 (1-2 players 2 decks, 3-4 four, 5-7 six, 8+ eight)'],
      ['penetration_pct', 'Cut card (% dealt)', 'num', [40, 95], 'The shoe is reshuffled once this much of it has been dealt'],
      ['blackjack_pays', 'Blackjack pays', 'select', [['3:2', '3 to 2'], ['6:5', '6 to 5'], ['1:1', '1 to 1'], ['2:1', '2 to 1']]],
      ['dealer_hits_soft_17', 'Dealer hits soft 17', 'bool'],
      ['dealer_peeks', 'Dealer peeks for blackjack', 'bool', null, 'Off: the hole card is only turned at the end, and a dealer blackjack takes the original bet only'],
      ['double_on', 'Double on', 'select', [['any', 'Any two cards'], ['9-11', '9, 10 or 11'], ['10-11', '10 or 11']]],
      ['double_after_split', 'Double after split', 'bool'],
      ['max_hands', 'Hands after splits (1 = no splits)', 'int', [1, 4]],
      ['resplit_aces', 'Resplit aces', 'bool'],
      ['split_aces_one_card', 'Split aces get one card', 'bool'],
      ['insurance', 'Insurance (2 to 1)', 'bool'],
      ['surrender', 'Late surrender', 'bool', null, 'Half the bet back, first decision only (needs the dealer peek)'],
      ['five_card_charlie', 'Five-card Charlie wins', 'bool'],
      ['open_bet_seconds', 'First bet window (s)', 'num', [5, 300]],
      ['bet_seconds', 'Next-hand window (s)', 'num', [5, 300], 'Bet again to keep your seat; a seat nobody bets on is freed when it closes'],
      ['insurance_seconds', 'Insurance window (s)', 'num', [3, 60]],
      ['action_seconds', 'First action round (s)', 'num', [4, 120], 'Everybody acts at once; the round ends when this runs out or everybody has chosen'],
      ['later_action_seconds', 'Later rounds (s)', 'num', [3, 120]],
      ['max_rounds', 'Max action rounds', 'int', [1, 20], 'After the last round everybody still in stands'],
      ['settle_seconds', 'Payout screen (s)', 'num', [3, 60]],
      ['summary_seconds', 'Table-closed card (s)', 'num', [3, 60]],
      ['card_ms', 'Deal speed (ms per card)', 'int', [100, 800]],
      ['idle_windows', 'Empty windows before it closes', 'int', [1, 50], 'Betting windows in a row with nobody betting close the table'],
      ['currency', 'Currency', 'text', 24],
      ['dealer_name', 'Name on the table plate', 'text', 24, 'Shown as NAME\'S TABLE on the rail (default HEX\'S TABLE)'],
      ['commands_text', 'Commands line on the overlay', 'text', 120, 'Your bot\'s commands, e.g. !bj 100 · !hit · !stand · !double · !split'],
      ['title', 'Title on the table', 'text', 32, 'Your branding: the plaque on the rail (Edit Mode can preview it)'],
      ['hide_when_idle', 'Hide between games', 'bool'],
      ['sfx', 'Overlay sound effects', 'bool'],
      ['sfx_volume', 'Sound volume', 'num', [0, 1]],
      ['deal_clip', 'Soundboard clip: deal', 'clip'],
      ['blackjack_clip', 'Soundboard clip: blackjack', 'clip'],
      ['win_clip', 'Soundboard clip: players win', 'clip']
    ],
    reasons: { double: 'double', split: 'split', insurance: 'insurance', push: 'push (stake back)', surrender: 'surrender (half back)' },
    textRows: {
      title: ['Title', 'Your branding: the plaque on the rail']
    },
    hint: 'Live mirror of the overlay. Hex deals a Vegas shoe game to up to 14 chatters at once: everybody acts together in timed rounds (hit, stand, double, split) and the server resolves them in seat order. Every bet is against the bank (your bot). The server shuffles and deals with a cryptographic RNG — nothing here can steer a card.',
    scene: 'table', ledgerNote: ', a push, a surrender, insurance', doc: 'blackjack',
    demoWord: 'hand',
    css: CSS,

    // ---- markup ----
    controls: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr;gap:9px;margin-top:14px">' +
        '<label class="f">Seats for this table <input type="number" id="' + id('seats') + '" min="1" max="14" placeholder="from settings"></label>' +
        '<label class="f">First bet window (s) <input type="number" id="' + id('secs') + '" min="5" max="300" placeholder="from settings"></label></div>' +
        '<div class="row"><button class="act" id="' + id('start') + '">Open table</button>' +
        '<button class="sec" id="' + id('test') + '" title="Plays exactly the same, but writes nothing to the ledger">Test table</button>' +
        '<button class="sec" id="' + id('close') + '" title="Finish the hand that is being played, then close the table">Close after this hand</button></div>';
    },
    play: function (id) {
      return '<div class="grid" style="grid-template-columns:1fr 1fr 1fr;gap:9px">' +
        '<label class="f">User <input id="' + id('pu') + '" placeholder="viewer" autocomplete="off"></label>' +
        '<label class="f">Amount <input type="number" id="' + id('pa') + '" min="1" step="1" placeholder="coins"></label>' +
        '<label class="f">Seat <input type="number" id="' + id('pseat') + '" min="1" max="14" step="1" placeholder="any"></label></div>' +
        '<div class="row"><button class="act" id="' + id('bet') + '">Bet</button><button class="sec" id="' + id('rebet') + '" title="The same ante as last hand">Rebet</button>' +
        '<button class="sec" id="' + id('cash') + '" title="Take the stake back and leave the table (during a hand: stand and leave after it)">Leave</button></div>' +
        '<div class="bj-act">' +
        [['hit', 'Hit'], ['stand', 'Stand'], ['double', 'Double'], ['split', 'Split'], ['insurance', 'Insurance'], ['decline', 'No insurance'], ['surrender', 'Surrender']]
          .map(function (a) { return '<button class="sec" data-act="' + a[0] + '" id="' + id('a-' + a[0]) + '">' + a[1] + '</button>'; }).join('') + '</div>';
    },
    extra: function (id) {
      return '<div class="card"><h2>Seats <span class="pill" id="' + id('seatpill') + '">—</span></h2>' +
        '<p class="hint">Every seat of the table, live: who sits where, what rides on it and where each hand stands. A seat is kept between hands only if its player bets again.</p>' +
        '<div class="bj-seats" id="' + id('seatgrid') + '"></div><div class="bj-q" id="' + id('queue') + '"></div></div>';
    },
    apiRows: function (a) {
      return [
        ['POST ' + a + '/start', '{seconds, seats (1–14), test} — open a table (409 if one is open)'],
        ['POST ' + a + '/bet', '{user, amount, seat?, balance?} — the ante: ledger debit; takes a seat (the first free one, or `seat`), else the queue; more adds to it'],
        ['POST ' + a + '/rebet', '{user} — the same ante as last hand (alias /ditto)'],
        ['POST ' + a + '/remove', '{user, amount?} — take back part (or all) of the stake before the deal'],
        ['POST ' + a + '/leave', '{user} — get up: stake back before the deal, else stands and leaves after the hand (alias /cashout)'],
        ['POST ' + a + '/action', '{user, action: hit|stand|double|split|surrender|insurance|decline, hand?, balance?} — ONE choice per hand per round; double / split / insurance debit at once (shortcuts /hit /stand …)'],
        ['POST ' + a + '/next', 'end the current window now · POST ' + a + '/close — close the table after this hand'],
        ['GET ' + a + '/table · /seats · /user/{name}', 'the STATE · seats + queue · one player, their hands and what they can do'],
        ['GET ' + a + '/ledger?since=0', 'this game\'s ledger events (tail it by seq and pay them)'],
        ['POST ' + a + '/stop', 'close the table now: every stake still on the line is returned'],
        ['POST ' + a + '/preview', '{overrides, seconds} — Test in OBS (Edit Mode): on screen for a few seconds with that look; never touches the game (/preview/clear ends it)'],
        ['GET ' + a + '/history · /bets · /validate', 'finished tables + stats · the rules, payouts, house edge and deck table · dry-run a bet']
      ];
    },
    examples: function (h) {
      return [
        'curl -X POST ' + h + '/start -H "Content-Type: application/json" -d "{\\"seats\\":10}"',
        'curl "' + h + '/bet?user=alice&amount=100"',
        'curl "' + h + '/bet?user=bob&amount=50&seat=3"',
        'curl "' + h + '/action?user=alice&action=hit"',
        'curl "' + h + '/action?user=bob&action=double"',
        'curl "' + h + '/rebet?user=alice"',
        'curl "' + h + '/ledger?since=0"'].join('\n');
    },

    // ---- wiring ----
    startBody: function (b, $$) {
      var n = parseInt($$('seats').value, 10), s = parseFloat($$('secs').value);
      if (isFinite(n)) b.seats = n;
      if (isFinite(s)) b.seconds = s;
    },
    betBody: function (b, $$) {
      var seat = parseInt($$('pseat').value, 10);
      if (isFinite(seat)) b.seat = seat;
    },
    wire: function (self, $$, who, pout) {
      // (the shared confirm talks about rides, which blackjack doesn't have)
      $$('stop').onclick = function () {
        if (!confirm('Stop the Blackjack table? Every stake still on the line is returned and a hand in progress is void.')) return;
        self.act('/stop', {}, 'Stopped');
      };
      $$('close').onclick = function () { self.act('/close', {}, function (d) { return d.closed ? 'Table closed' : 'The table closes after this hand'; }); };
      $$('rebet').onclick = function () {
        self.act('/rebet', { user: who() }, function (d) { return 'Bet ' + fmt(d.amount) + ' again (seat ' + (d.seat || 'queue') + ')'; }, pout);
      };
      $$('cash').onclick = function () {
        self.act('/leave', { user: who() }, function (d) {
          var c = (d.credits || [])[0];
          return d.left ? (c ? 'Left the table, ' + fmt(c.amount) + ' returned' : 'Left the table') : 'Leaves after this hand';
        }, pout);
      };
      var acts = ['hit', 'stand', 'double', 'split', 'insurance', 'decline', 'surrender'];
      acts.forEach(function (a) {
        var el = $$('a-' + a);
        if (el) el.onclick = function () {
          self.act('/action', { user: who(), action: a }, function (d) { return a + (d.cost ? ' — ' + fmt(d.cost) + ' debited' : '') + ' (seat ' + d.seat + ')'; }, pout);
        };
      });
      // the seats card follows every STATE
      var orig = self.render;
      self.render = function () { orig.call(this); try { fillSeats(this); } catch (e) { console.error('[games] blackjack seats card:', e); } };
      fillSeats(self);
    },
    onConfig: function (tab) { fillSeats(tab); },

    // ---- readouts ----
    sub: function (tab, g) {
      var seated = (g.seats || []).filter(function (s) { return s.user; }).length;
      return 'hand #' + Math.max(1, g.hand_no) + (g.round ? ' · round ' + g.round + ' of ' + g.rounds_max : '') + ' · ' + seated + '/' + g.seat_count + ' seats' +
        (g.queue && g.queue.length ? ' · ' + g.queue.length + ' waiting' : '') + (g.closing ? ' · closing after this hand' : '') + (g.test ? ' · TEST TABLE' : '');
    },
    facts: function (tab, g, cur) {
      var sh = g.shoe || {}, r = g.rules || {};
      return [
        ['On the line', fmt(g.at_risk) + ' ' + esc(cur)],
        ['Shoe', (sh.decks || '—') + ' decks · ' + (sh.left != null ? sh.left : '—') + ' cards' + (sh.cut_passed ? ' · cut card passed' : '')],
        ['Dealer shows', g.dealer && g.dealer.up ? esc(g.dealer.up.charAt(0).replace('T', '10') + {S: '♠', H: '♥', D: '♦', C: '♣'}[g.dealer.up.charAt(1)]) : '—'],
        ['House edge', '≈ ' + (r.edge_pct != null ? r.edge_pct.toFixed(2) : '—') + '%'],
        ['Blackjack pays', esc(r.blackjack_pays || '3:2') + (r.dealer_hits_soft_17 ? ' · H17' : ' · S17')]
      ];
    },
    idleFacts: function (idle) {
      var r = idle.rules || {};
      return [['Seats', idle.seats], ['Blackjack pays', esc(r.blackjack_pays || '3:2') + (r.dealer_hits_soft_17 ? ' · H17' : ' · S17')],
        ['House edge', '≈ ' + (r.edge_pct != null ? r.edge_pct.toFixed(2) : '—') + '%'],
        ['Bets', fmt(idle.min_bet) + (idle.max_bet ? ' – ' + fmt(idle.max_bet) : '+') + ' ' + esc(idle.currency || '')]];
    },
    playerRows: function (ps) {
      return '<tr><th class="num">Seat</th><th>User</th><th class="num">On the line</th><th>Hands</th><th class="num">Session</th></tr>' +
        ps.map(function (p) {
          return '<tr><td class="num">' + (p.seat || '—') + '</td><td>' + esc(p.user) + '</td><td class="num">' + fmt(p.stake) + '</td><td>' + esc(p.status) + '</td>' +
            '<td class="num">' + signed(p.net) + '</td></tr>';
        }).join('');
    },
    historyBits: function (s) {
      return [['Tables', s.games], ['Hands', s.hands], ['Player hands', s.player_hands], ['Blackjacks', s.blackjacks], ['Dealer busts', s.dealer_busts],
        ['Doubles', s.doubles], ['Splits', s.splits], ['Shoes', s.shoes], ['Wagered', fmt(s.total_bet)], ['Paid', fmt(s.total_paid)], ['House net', signed(s.house_net)]];
    },

    // ---- the editor ----
    rulesExtra: function (P) {
      return '<label class="tog"><input type="checkbox" id="' + P + 'show_shoe"> shoe & tray</label>' +
        '<label class="tog"><input type="checkbox" id="' + P + 'show_captions"> Hex\'s talk</label>';
    },
    extraChecks: ['show_shoe', 'show_captions'],
    sample: function (c, gid, ms) {
      var R = renderer();
      if (!R) throw new Error('blackjack renderer not loaded');
      // a hand in progress (cards down, a few choices made), the way it looks most of the time; the betting window as a fallback
      var np = Math.min(7, Math.max(3, Math.round(+c.seats || 10))), steps = R.demo(c, gid, { players: np }), i, st = null;
      for (i = 0; i < steps.length && !st; i++) if (steps[i].state.game.phase === 'action') st = JSON.parse(JSON.stringify(steps[i].state));
      if (!st) return R.sample(c, gid, ms, { players: np, held: true });
      st.game.deal = null; st.game.ends_in_ms = ms; st.game.phase_ms = Math.max(ms, 12000); st.game.elapsed_ms = 3000;
      return st;
    },
    preview: function (c, gid) {
      var R = renderer();
      if (!R) throw new Error('blackjack renderer not loaded');
      return R.demo(c, gid, { players: Math.min(6, Math.max(3, Math.round(+c.seats || 10))) });
    },
    demoTiming: function () { return null; }
  });
})();
