/* Games - top-bar dot (see docs/plugins.md). status = /games/api/status, or null.
   The tooltip says what each installed game is doing; games that are not installed
   simply are not in the status. */
(function () {
  // Games dot title, from /games/api/status alone: what each game is doing — a game
  // with a table (craps, roulette) also says its point, what is down and its countdown —
  // else the overlays.
  //   Games — roulette spinning
  //   Games — craps rolling, point is 6, 5 bets down (300 hexcoins) · roulette idle
  //   Games — craps: point is 6, 5 bets down (300 hexcoins) · 1 overlay connected
  //   Games — roulette: 3 bets down (150 hexcoins), spins in 12s · 1 overlay connected
  var GAME_WORDS = { spinning: 'spinning', result: 'showing result', cooldown: 'cooling down' };
  var GAME_OWN_WORDS = { craps: { spinning: 'rolling' },
    russian: { betting: 'taking bets', pulling: 'pulling the trigger', over: 'game over' },
    trivia: { betting: 'taking bets', question: 'question open', votes: 'answers locked', reveal: 'revealing', over: 'game over' } };

  function gameWord(key, state) {
    var own = GAME_OWN_WORDS[key];
    return (own && own[state]) || GAME_WORDS[state] || state;
  }

  function coins(v) {
    return String(Math.round(+v || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function tableNote(t) {
    if (!t || typeof t !== 'object') return '';
    var bits = [];
    if (t.phase === 'point' && t.point != null) bits.push('point is ' + t.point);
    var nb = Array.isArray(t.bets) ? t.bets.length : 0;
    if (nb) {
      bits.push(nb + (nb === 1 ? ' bet' : ' bets') + ' down' +
        (+t.total_on_table > 0 ? ' (' + coins(t.total_on_table) + ' ' + (t.currency || 'hexcoins') + ')' : ''));
    }
    if (typeof t.auto_roll_in_ms === 'number' && isFinite(t.auto_roll_in_ms)) {
      bits.push('auto-roll in ' + Math.max(0, Math.ceil(t.auto_roll_in_ms / 1000)) + 's');
    }
    // roulette's spin timer (auto-spin or started with /timer)
    if (typeof t.auto_spin_in_ms === 'number' && isFinite(t.auto_spin_in_ms)) {
      bits.push('spins in ' + Math.max(0, Math.ceil(t.auto_spin_in_ms / 1000)) + 's');
    }
    return bits.join(', ');
  }

  function gamesTitle(games, n) {
    games = games && typeof games === 'object' ? games : {};
    var busy = [], idle = [], notes = [];
    Object.keys(games).forEach(function (k) {
      var g = games[k];
      if (!g || typeof g !== 'object') return;
      var note = tableNote(g.table);
      if (g.state && g.state !== 'idle') {
        busy.push(k + ' ' + gameWord(k, g.state) + (note ? ', ' + note : ''));
      } else {
        idle.push(k + ' idle' + (note ? ', ' + note : ''));
        if (note) notes.push(k + ': ' + note);
      }
    });
    var overlays = n > 0 ? n + ' overlay' + (n === 1 ? '' : 's') + ' connected' : 'no overlay connected';
    if (busy.length) {
      return 'Games — ' + busy.concat(idle).join(' · ') + (n > 0 ? '' : ' (no overlay connected)');
    }
    return 'Games — ' + notes.concat([overlays]).join(' · ');
  }

  (window.HexbarStatus = window.HexbarStatus || {})['games'] = function (s) {
    if (!s) return { on: false, warn: false, title: 'Games - not responding' };
    var n = +s.overlays || 0;
    return { on: n > 0, warn: true, title: gamesTitle(s.games, n) };
  };
})();
