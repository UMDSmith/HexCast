/*
 * Hexcast Games — Soul Climb renderer                          static/climb.js
 *
 * Shared by the OBS overlay (/games/overlay) and the control panel (/games#climb).
 * Plain browser script: no modules, no dependencies, no build step.
 *
 * Registers window.HexGames.climb:
 *
 *   BASE_W, BASE_H            scene size in stage px at scale 1 (880 x 960)
 *   THEMES                    { inferno, abyss, sulfur }
 *   DEFAULTS, APPEARANCE      same values / keys as the backend
 *   create(container, config, opts) -> instance      opts: {sound: true}
 *   demoSteps(config, id)     the Edit Mode editor's ▶ Preview: [{ms, state}] of a short local climb
 *
 *   instance.setConfig(cfg)   instance.resize()    instance.setState(STATE)
 *   instance.reset()          instance.destroy()
 *
 * setState() takes the game's whole STATE (the server's state_view(): {state, visible, game, idle, ...});
 * game.ends_in_ms / game.elapsed_ms must already be corrected for the time the message sat in the host.
 * Everything on screen is a function of the state and the time since its phase started, so a late join
 * (or the panel's mirror) lands on the same frame as every other overlay.
 *
 * THE SCRIPT. While the soul climbs, game.script (built by the server, see climb.py) is a list of timed
 * beats {t, d, ev, lv, to, s, ...}: this renderer replays it. The OUTCOME (script.max, the best height) was
 * decided by the server's survival model before the script was made; everything funny here - the route he
 * picks, the rests, the demons, the near falls, the bats, the geyser, the dead end, the skeleton hand, the
 * way he finally falls - is dressing that never goes above that height. The renderer never decides
 * anything: it only draws what the script says, with the script's seeds for its own little choices
 * (which line he mutters, how a rock crumbles).
 *
 * Layout (scene px): the pit viewport (0..600 x 0..960) on the left with the camera following the soul up
 * the wall (three parallax layers, flags on ledges for every bet), and a column on the right (title, timer,
 * height meter, bets, results).
 */
(function (root) {
  'use strict';

  var HG = root.HexGames || (root.HexGames = {});
  var BASE_W = 880, BASE_H = 960;
  var VW = 600, VH = 960;                 // the pit viewport (scene px)
  var COL_X = 620, COL_W = 260;           // the HUD column
  var LV = 56;                            // px per level
  var LANE_X = 212;                       // the climbing line in the viewport
  var ANCHOR_Y = 650;                     // viewport y of the soul's pelvis when the camera is on him
  var FOOT = 40;                          // pelvis above the feet when he stands
  var TAIL_MS = 600;                      // the climbing phase lasts the script + this (climb.py CL_TAIL_S)
  var TAU = Math.PI * 2;

  var DEFAULTS = {
    x: 50, y: 50, scale: 1.0, theme: 'inferno', title: 'Soul Climb', show_rules: true, show_players: true,
    players_max: 8, show_odds: true, sfx: true, sfx_volume: 0.6, hide_when_idle: true, commands_text: '',
    climbs: 1, max_height: 100, escape_pct: 2, house_edge_pct: 5, result_seconds: 8, summary_seconds: 12,
    climb_speed: 1.0, currency: 'coins', soul_name: ''
  };
  var APPEARANCE = ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'show_odds',
    'sfx', 'sfx_volume'];

  // ------------------------------------------------------------------ helpers
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function merge(a, b) { var o = {}, k; for (k in a) if (has(a, k)) o[k] = a[k]; if (b) for (k in b) if (has(b, k) && b[k] != null) o[k] = b[k]; return o; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function sat01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function smooth(a, b, v) { var t = sat01((v - a) / (b - a)); return t * t * (3 - 2 * t); }
  function ease(t) { t = sat01(t); return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
  function easeOut(t) { t = sat01(t); return 1 - Math.pow(1 - t, 3); }
  function easeIn(t) { t = sat01(t); return t * t; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmt(n) { n = Math.round(+n || 0); return String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',').replace(/^/, n < 0 ? '−' : ''); }
  function mult(m) { return '×' + (+m).toFixed(2); }
  function mulberry(a) {
    return function () {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function hash(n) {              // integer -> [0,1), deterministic
    n = (n | 0) ^ 0x9e3779b9; n = Math.imul(n ^ n >>> 16, 0x85ebca6b); n = Math.imul(n ^ n >>> 13, 0xc2b2ae35);
    return ((n ^ n >>> 16) >>> 0) / 4294967296;
  }
  function hash2(a, b) { return hash((a | 0) * 73856093 ^ (b | 0) * 19349663); }
  function pick(arr, r) { return arr[Math.floor(r * arr.length) % arr.length]; }
  function now() { return (root.performance && root.performance.now) ? root.performance.now() : Date.now(); }
  var raf = root.requestAnimationFrame ? function (f) { return root.requestAnimationFrame(f); } : function (f) { return setTimeout(function () { f(now()); }, 16); };
  var caf = root.cancelAnimationFrame ? function (id) { root.cancelAnimationFrame(id); } : function (id) { clearTimeout(id); };

  // ------------------------------------------------------------------ themes
  // sky0/sky1: the glow at the foot of the pit and the dusk higher up; skyTop: the daylight at the very top.
  // strata: the rock layers (every 14 levels the next one); lava*: the lava; the rest is the HUD's.
  var THEMES = {
    inferno: {
      sky0: '#1a0507', sky1: '#2a0d1c', skyTop: '#f2b27a', lava0: '#ff3a10', lava1: '#ff9b1f', lava2: '#fff0a2', lavaDeep: '#5a0a04',
      glow: '255,96,28', strata: ['#3a1a1c', '#44302e', '#4f4034', '#2d2238', '#323a4c', '#4c5a70'], rockHi: '#7a5a52', rockLo: '#150709',
      bone: '#e9dcc0', bone2: '#b9a77f', iron: '#5b5660', ironHi: '#9a94a2', rope: '#b98a54', rope2: '#7a5530', flag: '#ffb03a',
      accent: '#ffae3b', accent2: '#ff5a2a', ink: '#fff3e4', dim: '#dcb7a2', panel: 'rgba(26,9,12,.9)', line: 'rgba(255,150,70,.5)',
      good: '#7df09a', bad: '#ff6b5a', font: "'Trebuchet MS','Segoe UI',Verdana,sans-serif", textGlow: '0 0 12px rgba(255,120,40,.55)'
    },
    abyss: {
      sky0: '#050818', sky1: '#10153a', skyTop: '#9ec8ff', lava0: '#3a6bff', lava1: '#43d2ff', lava2: '#e8fbff', lavaDeep: '#0a1c5a',
      glow: '70,150,255', strata: ['#1d2146', '#262c52', '#2f3a5c', '#3a2a58', '#22425a', '#43587a'], rockHi: '#5d6aa0', rockLo: '#04061a',
      bone: '#e4ecff', bone2: '#a9b6d8', iron: '#5a6082', ironHi: '#a6b0d8', rope: '#9aa7d8', rope2: '#5b6896', flag: '#7ec8ff',
      accent: '#7fd2ff', accent2: '#8f7bff', ink: '#eef4ff', dim: '#aab8e0', panel: 'rgba(8,12,34,.9)', line: 'rgba(120,170,255,.5)',
      good: '#7dffc8', bad: '#ff7fa0', font: "'Trebuchet MS','Segoe UI',Verdana,sans-serif", textGlow: '0 0 12px rgba(110,170,255,.6)'
    },
    sulfur: {
      sky0: '#141003', sky1: '#2a2406', skyTop: '#fff3a0', lava0: '#e6b800', lava1: '#ffe14a', lava2: '#fffbd0', lavaDeep: '#5a4a00',
      glow: '255,210,40', strata: ['#3a3412', '#463f1c', '#52472a', '#332f2a', '#3a4034', '#505c44'], rockHi: '#8a7d3c', rockLo: '#100d02',
      bone: '#f1e7c4', bone2: '#bdb07a', iron: '#5c5a52', ironHi: '#a8a496', rope: '#c8a45a', rope2: '#7e6430', flag: '#ffd84a',
      accent: '#ffd23f', accent2: '#8fdc3a', ink: '#fffbe0', dim: '#d8cc90', panel: 'rgba(22,18,4,.9)', line: 'rgba(255,214,70,.5)',
      good: '#9bff7a', bad: '#ff7a4a', font: "'Trebuchet MS','Segoe UI',Verdana,sans-serif", textGlow: '0 0 12px rgba(255,210,60,.55)'
    }
  };

  // soul looks: skin colours and gear (the server picks indexes per climb)
  var SKINS = [
    { a: '#a39fa4', b: '#575160', belly: '#cfc9cb' },      // ashen
    { a: '#a6a77a', b: '#56583f', belly: '#d3d1a4' },      // sallow
    { a: '#93a3b8', b: '#47566a', belly: '#c4d0e0' },      // blue-grey
    { a: '#b89a98', b: '#65494b', belly: '#e0c2bc' },      // flayed
    { a: '#bdae84', b: '#625839', belly: '#e6dab0' },      // bone-yellow
    { a: '#92a997', b: '#44574c', belly: '#bfd1c2' }       // grave-green
  ];
  var GEARS = ['none', 'tie', 'hardhat', 'headband', 'glasses', 'party', 'bow', 'scarf'];

  // ------------------------------------------------------------------ captions
  // What he, the demons and the pit say. One is picked per beat with the beat's own seed ({n} = his name).
  var LINES = {
    ready: ['Okay. Okay okay okay.', 'Here goes nothing.', 'I can do this. Probably.', 'Is it too late to bring a rope?',
      '{n}, you got this. (You don\'t.)', 'Just one little pit.', 'Warm-up\'s over. Mostly.', 'Why is the floor lava?',
      'Pit wall. How hard can it be?', 'I should have stretched.', 'Please let it be a short pit.'],
    effort: ['Ugh!', 'Hnngh!', 'Almost there...', 'This is fine.', 'Why is the wall warm?', 'Just. One. More. Ledge.', 'Don\'t look down.',
      'My arms have filed a complaint.', 'Is that a ledge or a tooth?', 'Heh. Easy.', 'Should have taken the stairs.',
      'Up, up, up...', 'Ow ow ow ow.', 'I regret everything.', 'Who built this?', 'Pull. Pull. Pull.', 'Okay, that one was sharp.',
      'Left hand, right hand...', 'Note to self: gym.', 'Nice view. Of lava.', 'Still better than Mondays.', 'I felt that in my soul.'],
    rest: ['Break time.', '*pant pant*', 'Union rules: a break.', 'Anyone have water?', 'My arms are noodles.', 'Is it Friday yet?',
      'Five minutes. Just five.', 'Great ledge. Five stars.', 'Hello, ledge. Don\'t crumble.', 'I earned this nap. Sort of.',
      'Whew... whoo...', 'Just resting my eyes.'],
    idle_shrug: ['Meh.', '¯\\_(ツ)_/¯', 'Who knows!', 'Eh. Could be worse.'],
    idle_wipe: ['Phew, sweaty.', 'It\'s a dry heat. A VERY dry heat.', 'So hot. So, so hot.', '*wipes brow*'],
    idle_pant: ['*huff huff*', 'Cardio is a lie.', 'Whoo...', 'Need. Air.'],
    idle_gulp: ['*gulp*', 'Don\'t look down. Looked down.', 'That\'s... far.', 'I can see my house. (I can\'t.)'],
    idle_wave: ['Hi chat!', 'Hello, viewers!', 'Wave if you believe in me!', 'Hi mom!', 'Tell them I tried!'],
    idle_flex: ['Check out these guns.', 'Gains!', 'Pit-ready.', 'Flex. Flex. Flex.'],
    cheer: ['Flag! Ha!', 'Ding! Pay up!', 'Got it!', 'Called it!', 'Take that, gravity!', 'Somebody owes me coins.', 'Next!', 'Yes! YES!'],
    taunt: ['Is that your best?', 'Nice climbing, noodle arms.', 'I\'ve seen snails climb faster.', 'Management says NO.', 'Bet he falls at 12.',
      'Hey! You dropped your dignity.', 'Ooh, so close. Not.', 'Going up? I\'m going to say no.', 'My grandma climbs better. She\'s dead.',
      'Great form! For a pancake.', 'Everyone falls eventually.', 'Look at him go! (Slowly.)', 'Tickets are non-refundable.', 'Hey {n}! Down here is nicer!'],
    taunt_reply: ['Not helping!', 'Rude.', 'Nobody asked you!', 'I can hear you!', 'Watch this!', 'Hmph.', 'You\'re not my supervisor.', 'La la la, not listening.'],
    slip: ['WHOA!', 'Eep!', 'Not today!', 'Nope nope nope!', 'I meant to do that.', 'AAAH— oh. Okay.', 'That was a ledge, right?!', 'Whoopsie!',
      'Still here! Still here!', 'Gravity, not now!', 'Ha! Nice try, wall!'],
    bat: ['Shoo! SHOO!', 'Not the face!', 'I\'m allergic to bats!', 'BATS?!', 'Get off! Get OFF!', 'Rude little fliers!', 'Eek! Wings!', 'I have no snacks!'],
    geyser: ['HOT HOT HOT!', 'Lava! Rude.', 'Who put a geyser there?!', 'My eyebrows!', 'Sunburn!', 'Okay okay that\'s warm.', 'Yikes yikes yikes!'],
    deadend: ['Is this a dead end?', 'Spikes. Of course.', 'Wrong turn! WRONG TURN!', 'Nobody saw that. Nobody.', 'The map said left!', 'Ow. Spikes.', 'Let\'s pretend that never happened.'],
    grab: ['Let go of my ankle!', 'Hey! Bony! Rude!', 'I didn\'t even wave!', 'Get off, Skeleton Steve!', 'Not the foot!', 'We\'re not friends!', 'Hands to yourself!'],
    fray: ['Creak... creak...', 'That rope looks tired.', 'Please hold, please hold.', 'I did NOT read the weight limit.', 'Is that thread supposed to snap?', 'Pop. Okay. Fine.'],
    fatal_rock: ['This ledge is... crumbly.', 'Uh oh. Pebbles.', 'Is the wall... moving?', 'That\'s not a good sound.'],
    fatal_hand: ['Oh. Hello, hand.', 'Wait—not again!', 'Is that a... bony handshake?', 'We just met!'],
    fatal_rope: ['Please hold...', 'Rope? Rope? ROPE?!', 'Creak. Creak. Twang.', 'Tell the rope I said sorry.'],
    fatal_chain: ['Clink. Clank. Uh-oh.', 'That link is... missing.', 'Chain? Chain!!', 'Hold it together, chain!'],
    fatal_rib: ['That\'s a funny-sounding rib.', 'Rib? Rib?! RIB!', 'Bone doesn\'t bend like that.', 'Crack. Oh no.'],
    fatal_bat: ['Not again, bats!', 'Please, I have a family! (I don\'t.)', 'Too many wings!', 'I just wanted to go up!'],
    fatal_geyser: ['Wait, is that—', 'HOTHOTHOTHOT!', 'Of course. A geyser.', 'I feel a rumble...'],
    fatal_demon: ['Oh no. A demon.', 'Don\'t push me!', 'Please? Pretty please?', 'I\'m going up, thanks!'],
    fatal_tired: ['Arms... giving... out...', 'Can\'t... hold...', 'I\'m so tired...', 'Just five more minutes...', 'My fingers have a mind of their own.'],
    fall: ['Oh no.', 'Ohhh noooo...', 'AAAAAAH!', 'Tell my mom I tried!', 'Not like thiiis!', 'I regret nothing! (I regret everything.)',
      'This is fine!', 'Wheeeee—oh no.', 'Down is also a direction!', 'See you in a bit!', 'Tell them I was close!'],
    bonk: ['Bonk!', 'Ow!', 'Oof!', 'Bonk. Ow. BONK.', 'Ow! Ow! Ow!', 'Pardon me!', 'Boing—OW!'],
    splash: ['SPLASH!', 'Glub.', 'Warm, at least.', 'Soup\'s on!', 'Just a quick dip...'],
    flick: ['*FLICK*', 'Not again!', 'Go away, little one.', 'Back you go!', 'Nice try. Next!'],
    grinder: ['Not the grinder!', 'It\'s... squishy.', 'Sausage?!', 'Link me up.', 'Ohhh... my back.'],
    boing: ['Boing!', 'Boing! Boing!', 'Wheee!', 'Is this... fun?', 'Boing... boing... oh.'],
    umbrella: ['Mary Poppins mode!', 'Hello, down there!', 'Float, float...', 'Parachute? Close enough!', 'It\'s getting warm...', 'Oh no, it\'s on fire.'],
    escape: ['FREEDOM!!!', 'I\'M OUTTA HERE!', 'Never again!', 'Tell the demons I\'ll write!', 'I DID IT!', 'Fresh air! Fresh... smell of grass!', 'Goodbye, pit!'],
    demon_win: ['Next!', 'Better luck next soul.', 'Mine now.', 'Back to the pit with you.']
  };
  var SINS = ['reply-all', 'double-dipping', 'spoiling the finale', 'not rewinding', 'parking across two spots', 'microwaving fish',
    'talking in cinemas', 'skipping the line', 'leaving 1 sip of milk', 'clapping on 1 and 3', 'liking their own post',
    'being early', 'saying "per my last email"', 'recliner abuse', 'gatekeeping brunch', 'owning a hot-air popper'];

  function fillName(s, name) { return String(s).replace(/\{n\}/g, name || 'Gary'); }

  // ==================================================================== the timeline
  // The script, indexed: where the soul is (level, in float levels) at any time, what he has reached, when
  // a flag turns green. Pure functions of the script and the time - the same on every overlay.
  function Timeline(script, name) {
    var beats = (script && script.beats) || [], n = beats.length, i, b;
    this.script = script || { beats: [], max: 0, height: 100, duration_ms: 0, routes: [] };
    this.beats = beats;
    this.starts = new Array(n);
    this.D = +this.script.duration_ms || (n ? beats[n - 1].t + beats[n - 1].d : 0);
    this.max = +this.script.max || 0;
    this.H = +this.script.height || 100;
    this.seed = (this.script.seed | 0) || 1;
    this.fallAt = Infinity; this.fallBeat = null; this.fatalBeat = null; this.escapeBeat = null;
    this.arrive = [0];               // arrive[h]: ms when he first is at level h (h = 0..max)
    var top = 0;
    for (i = 0; i < n; i++) {
      b = beats[i];
      this.starts[i] = b.t;
      if (b.ev === 'fall') { this.fallAt = b.t; this.fallBeat = b; }
      if (b.ev === 'fatal') this.fatalBeat = b;
      if (b.ev === 'escape') this.escapeBeat = b;
      if (b.ev === 'climb') {
        if (b.to > top) { this.arrive[b.to] = b.t + b.d; top = b.to; }
      } else if (b.ev === 'deadend' && b.peak > top) {
        var span = b.peak - b.lv;
        for (var l = top + 1; l <= b.peak; l++) this.arrive[l] = b.t + b.d * 0.45 * (l - b.lv) / span;
        top = b.peak;
      }
    }
    this.top = top;
    this.props = []; this.loose = {}; this.crack = {}; this.rope = {}; this.bubbles = [];
    this._extras(name);
  }
  var TP = Timeline.prototype;
  TP.index = function (t) {                      // the beat that is on at t (clamped to the first / last)
    var s = this.starts, lo = 0, hi = s.length - 1;
    if (hi < 0) return -1;
    if (t <= s[0]) return 0;
    while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (s[mid] <= t) lo = mid; else hi = mid - 1; }
    return lo;
  };
  // 0..1 progress through beat b at time t
  TP.u = function (b, t) { return b.d > 0 ? sat01((t - b.t) / b.d) : 1; };
  // The best level reached by time t (an integer): flags up to it are passed.
  TP.reached = function (t) {
    var a = this.arrive, lo = 0, hi = a.length - 1;
    if (t < 0) return 0;
    while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (a[mid] <= t) lo = mid; else hi = mid - 1; }
    return lo;
  };
  // 'passed' | 'lost' | 'open' for a flag at height h at time t
  TP.flag = function (h, t) {
    if (h <= this.top && this.arrive[h] != null && this.arrive[h] <= t) return 'passed';
    if (t >= this.fallAt) return 'lost';
    return 'open';
  };
  // The pelvis's level (float) at time t. Never above script.max: every case below is bounded by its beat's levels.
  TP.level = function (t) {
    var i = this.index(t);
    if (i < 0) return 0;
    var b = this.beats[i], u = this.u(b, t), lv = b.lv, to = b.to;
    switch (b.ev) {
      case 'climb': return lv + (to - lv) * ease((u - 0.25) / 0.75);
      case 'slip': {
        // the foot gives: a fast slide down, a catch, a little bounce (never below the level he lands on)
        var k = u < 0.5 ? easeIn(u / 0.5) : 1;
        var bounce = u >= 0.5 ? Math.sin(sat01((u - 0.5) / 0.5) * Math.PI) * 0.12 : 0;
        return Math.max(to, lv + (to - lv) * k + bounce * (lv > to ? 1 : 0));
      }
      case 'deadend': {
        var peak = b.peak != null ? b.peak : lv;
        var f = u < 0.45 ? ease(u / 0.45) : u < 0.6 ? 1 : 1 - ease((u - 0.6) / 0.4);
        return lv + (peak - lv) * f;
      }
      case 'fall': return fallLevel(this, b, t);
      default: return Math.max(0, lv);
    }
  };

  // The fall: from level lv (the best height) to the bottom. fallLevel() is the pelvis; the style's own
  // choreography (bonks, the cauldron, the grinder ...) lives in the drawing. impact = when he is at the bottom.
  var FALL_IMPACT = { bonk: 0.78, yelp: 0.58, cauldron: 0.5, flick: 0.5, grinder: 0.46, boing: 0.45, umbrella: 0.66 };
  function fallLevel(tl, b, t) {
    var u = tl.u(b, t), st = b.style || 'yelp', imp = FALL_IMPACT[st] || 0.55, lv = b.lv;
    if (st === 'bonk') {
      // down the wall in n hops: each hop is a bounce off a ledge, a little out and a little up again
      var n = Math.max(1, b.n || 1), v = sat01(u / imp) * n, k = Math.min(n - 1, Math.floor(v)), f = v - k;
      var top = lv * (1 - k / n), bot = lv * (1 - (k + 1) / n);
      var arc = Math.sin(f * Math.PI) * Math.min(1.2, 0.18 * (top - bot) + 0.3);
      // the rebound off a ledge only after the first one, and never above where the fall began (= the best height)
      return Math.min(lv, Math.max(-0.3, lerp(top, bot, easeIn(f) * 0.8 + f * 0.2) + arc * (k > 0 ? 1 : 0)));
    }
    var g = sat01(u / imp);
    var h = lv * (1 - g * g);                        // free fall: down the wall, faster and faster
    return Math.max(-0.4, h);
  }

  // ==================================================================== the soul
  // An emaciated damned man, drawn from a pose (limb targets in screen px) with two-bone IK: tapered limbs shaded across
  // their width (cool fill from above, a warm lava rim from below), a ribcage and clavicles, five-fingered hands that curl
  // round their holds, a tattered loincloth, a gaunt face whose expression follows the mood. The comedy is in the acting.
  var SOUL = { a1: 25, a2: 23, l1: 30, l2: 30, shX: 18, shY: -42, hipX: 8, headY: -66 };
  var INK = '#1a0c10';

  function ik(sx, sy, tx, ty, l1, l2, side) {
    // two-bone IK; side -1 = the elbow / knee points left, +1 = right, 2 = down (screen). Returns elbow + the (clamped) end.
    var dx = tx - sx, dy = ty - sy, d = Math.sqrt(dx * dx + dy * dy) || 0.001;
    var maxd = l1 + l2 - 0.4, mind = Math.abs(l1 - l2) + 1;
    var dd = clamp(d, mind, maxd), a = Math.atan2(dy, dx);
    var cosA = (l1 * l1 + dd * dd - l2 * l2) / (2 * l1 * dd), ang = Math.acos(clamp(cosA, -1, 1));
    var e1x = sx + Math.cos(a + ang) * l1, e1y = sy + Math.sin(a + ang) * l1;
    var e2x = sx + Math.cos(a - ang) * l1, e2y = sy + Math.sin(a - ang) * l1;
    var pickFirst = side === 2 ? e1y > e2y : side < 0 ? e1x < e2x : e1x > e2x;     // side 2 = the elbow points down
    return { ex: pickFirst ? e1x : e2x, ey: pickFirst ? e1y : e2y, hx: sx + dx / d * dd, hy: sy + dy / d * dd };
  }

  function rr(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y); c.lineTo(x + w - r, y); c.quadraticCurveTo(x + w, y, x + w, y + r);
    c.lineTo(x + w, y + h - r); c.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    c.lineTo(x + r, y + h); c.quadraticCurveTo(x, y + h, x, y + h - r);
    c.lineTo(x, y + r); c.quadraticCurveTo(x, y, x + r, y); c.closePath();
  }

  // skin ramps: hi (cool light from above), a (mid), b (shadow); the lava rim is added from the theme
  function skinRamp(sk) { return sk.hi ? sk : (sk.hi = mixc(sk.belly, '#9db4d8', 0.28), sk); }
  function rimCol(L, al) { return 'rgba(' + L.glow + ',' + clamp(al * L.rim, 0, 1).toFixed(3) + ')'; }
  // the stops of a gradient that runs across a shape from its screen-top edge (0) to its screen-bottom edge (1)
  function skinStops(g, sk, L, flip) {
    var st = [[0, sk.hi], [0.28, mixc(sk.hi, sk.a, 0.6)], [0.5, sk.a], [0.78, sk.b], [0.9, mixc(sk.b, 'rgb(' + L.glow + ')', 0.35 * L.rim)], [1, mixc(sk.b, 'rgb(' + L.glow + ')', 0.8 * L.rim)]];
    for (var i = 0; i < st.length; i++) g.addColorStop(flip ? 1 - st[st.length - 1 - i][0] : st[i][0], st[flip ? st.length - 1 - i : i][1]);
  }

  // a tapered limb segment, shaded across its width
  function seg(c, x0, y0, x1, y1, w0, w1, sk, L, o) {
    var dx = x1 - x0, dy = y1 - y0, len = Math.sqrt(dx * dx + dy * dy) || 0.01, a = Math.atan2(dy, dx), down = Math.cos(a + L.rot) >= 0;
    c.save(); c.translate(x0, y0); c.rotate(a);
    var wm = Math.max(w0, w1) / 2, g = c.createLinearGradient(0, -wm, 0, wm);
    skinStops(g, sk, L, !down);
    c.beginPath(); c.moveTo(0, -w0 / 2); c.lineTo(len, -w1 / 2); c.arc(len, 0, w1 / 2, -Math.PI / 2, Math.PI / 2); c.lineTo(0, w0 / 2); c.arc(0, 0, w0 / 2, Math.PI / 2, Math.PI * 1.5);
    c.closePath(); c.fillStyle = g; c.fill(); c.lineWidth = 1; c.strokeStyle = 'rgba(14,6,9,.6)'; c.stroke();
    if (o && o.tendons) {                                  // sinews standing out along the limb
      c.lineCap = 'round';
      for (var i = -1; i <= 1; i += 2) {
        var ww = (w0 + w1) / 2 * 0.26 * i;
        c.strokeStyle = 'rgba(14,6,9,' + (0.18 + 0.4 * o.tendons) + ')'; c.lineWidth = 0.9;
        c.beginPath(); c.moveTo(len * 0.18, ww * 0.8); c.quadraticCurveTo(len * 0.5, ww * 1.5, len * 0.88, ww * 0.5); c.stroke();
        c.strokeStyle = 'rgba(255,235,215,' + (0.06 + 0.12 * o.tendons) + ')'; c.beginPath(); c.moveTo(len * 0.18, ww * 0.8 - 1); c.quadraticCurveTo(len * 0.5, ww * 1.5 - 1, len * 0.88, ww * 0.5 - 1); c.stroke();
      }
    }
    if (o && o.bulge) {                                    // a muscle belly (straining)
      var bg = c.createRadialGradient(len * 0.4, -w0 * 0.12, 0.5, len * 0.4, 0, w0 * 0.75);
      bg.addColorStop(0, 'rgba(255,240,225,' + (0.16 * o.bulge) + ')'); bg.addColorStop(1, 'rgba(255,240,225,0)');
      c.fillStyle = bg; c.fillRect(len * 0.1, -w0, len * 0.7, w0 * 2);
    }
    c.restore();
  }
  function joint(c, x, y, r, sk, L, soft) {
    // soft = a muscle cap that blends into the limb (shoulders); otherwise just the bony highlight of an elbow / knee
    if (soft) {
      var g = c.createRadialGradient(x - r * 0.3, y - r * 0.5, 0.5, x, y, r * 1.2);
      g.addColorStop(0, sk.hi); g.addColorStop(0.6, sk.a); g.addColorStop(1, sk.b);
      c.fillStyle = g; c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill();
      c.strokeStyle = rimCol(L, 0.45); c.lineWidth = 1; c.beginPath(); c.arc(x, y, r * 0.85, 0.5, Math.PI - 0.5); c.stroke();
      return;
    }
    c.fillStyle = 'rgba(235,222,205,.16)'; c.beginPath(); c.ellipse(x - r * 0.1, y - r * 0.2, r * 0.7, r * 0.55, 0, 0, TAU); c.fill();
    c.strokeStyle = 'rgba(14,6,9,.32)'; c.lineWidth = 1; c.beginPath(); c.arc(x, y, r * 0.8, 0.6, Math.PI - 0.6); c.stroke();
  }

  // a hand: palm, four fingers of three phalanges and a thumb. grip 1 = curled round the hold, 0 = open and spread
  function hand(c, x, y, ang, grip, sk, L, sd) {
    c.save(); c.translate(x, y); c.rotate(ang);
    var ca = Math.cos(ang + L.rot), cs = Math.abs(ca) > 0.3 ? (ca > 0 ? 1 : -1) : (sd || 1);       // curl towards the screen-bottom
    var palm = c.createLinearGradient(0, -5, 0, 5); skinStops(palm, sk, L, ca < 0);
    c.fillStyle = palm; c.strokeStyle = 'rgba(14,6,9,.6)'; c.lineWidth = 1;
    c.beginPath(); c.moveTo(-1.5, -3.4 * cs); c.lineTo(8, -4.6 * cs); c.quadraticCurveTo(10.4, 0, 8, 4.6 * cs); c.lineTo(-1.5, 3.4 * cs); c.closePath(); c.fill(); c.stroke();
    var lens = [10, 12, 11, 8.4], fr = [0.42, 0.33, 0.25], i, j;
    c.lineCap = 'round'; c.lineJoin = 'round';
    for (i = 0; i < 4; i++) {
      var by = (-3.3 + i * 2.2) * cs, a0 = (i - 1.5) * 0.13 * (1 - grip) * cs, px = 8.6, py = by, pa = a0, pts = [[px, py]];
      for (j = 0; j < 3; j++) {
        pa += cs * grip * (0.5 + 0.38 * j);
        px += Math.cos(pa) * lens[i] * fr[j]; py += Math.sin(pa) * lens[i] * fr[j]; pts.push([px, py]);
      }
      c.beginPath(); c.moveTo(pts[0][0], pts[0][1]); c.lineTo(pts[1][0], pts[1][1]); c.lineTo(pts[2][0], pts[2][1]); c.lineTo(pts[3][0], pts[3][1]);
      c.strokeStyle = 'rgba(14,6,9,.75)'; c.lineWidth = 3.7; c.stroke(); c.strokeStyle = sk.a; c.lineWidth = 2.6; c.stroke();
      if (grip > 0.5) { c.fillStyle = 'rgba(235,222,205,.35)'; c.beginPath(); c.arc(pts[1][0], pts[1][1] - cs * 1.2, 1, 0, TAU); c.fill(); }
    }
    var tp = [[2.4, -4.2 * cs]], ta = -cs * (0.5 - 0.5 * grip) - 0.25 * cs;
    for (j = 0; j < 2; j++) { ta += cs * grip * 0.55; tp.push([tp[j][0] + Math.cos(ta - cs * 0.0) * 5.4, tp[j][1] + Math.sin(ta) * 5.4 - cs * 0.9 * (1 - grip) * (j ? 0 : 1)]); }
    for (var ps = 0; ps < 2; ps++) for (j = 0; j < 2; j++) {
      c.strokeStyle = ps ? sk.a : 'rgba(14,6,9,.75)'; c.lineWidth = (3.4 - j * 0.6) + (ps ? 0 : 1.1);
      c.beginPath(); c.moveTo(tp[j][0], tp[j][1]); c.lineTo(tp[j + 1][0], tp[j + 1][1]); c.stroke();
    }
    c.restore();
  }

  // a foot seen from the front: heel and ankle bones, the arch, five toes that curl onto a hold
  function foot(c, x, y, ang, sk, L, grip) {
    c.save(); c.translate(x, y); c.rotate(ang);
    var g = c.createLinearGradient(0, -3, 0, 9); skinStops(g, sk, L, false);
    c.fillStyle = g; c.strokeStyle = 'rgba(14,6,9,.6)'; c.lineWidth = 1;
    c.beginPath(); c.moveTo(-4.2, -1); c.quadraticCurveTo(-7.4, 4, -6.4, 8.6); c.lineTo(6.4, 8.6); c.quadraticCurveTo(7.6, 4, 4.2, -1); c.closePath(); c.fill(); c.stroke();
    var cl = 0.5 + 0.5 * (grip == null ? 0.5 : grip);
    for (var i = -2; i <= 2; i++) {
      var tx = i * 2.9, ty = 8.4 + (i === -2 ? 0.4 : Math.abs(i) * 0.15), tg = c.createLinearGradient(0, ty - 2, 0, ty + 3.4);
      tg.addColorStop(0, sk.a); tg.addColorStop(1, mixc(sk.b, 'rgb(' + L.glow + ')', 0.5 * L.rim));
      c.fillStyle = tg; c.beginPath(); c.ellipse(tx, ty + 1.2 * cl, 1.7 + (i === -2 ? 0.5 : 0), 2.6 * (0.8 + 0.2 * cl), 0, 0, TAU); c.fill(); c.stroke();
      c.fillStyle = 'rgba(40,28,24,.8)'; c.beginPath(); c.ellipse(tx, ty + 2.6 * cl, 0.9, 0.7, 0, 0, TAU); c.fill();
    }
    c.fillStyle = 'rgba(235,222,205,.3)'; c.beginPath(); c.arc(-3.6, 1.4, 1.5, 0, TAU); c.arc(3.6, 1.4, 1.5, 0, TAU); c.fill();
    c.strokeStyle = rimCol(L, 0.6); c.lineWidth = 1; c.beginPath(); c.moveTo(-6, 8.6); c.lineTo(6, 8.6); c.stroke();
    c.restore();
  }

  // the ribcage and shoulders: collarbones, sternum, curved ribs with dark gaps, a sunken belly, hip bones
  function torso(c, sk, L, t) {
    var br = Math.sin(t / 640) * 0.5, sx = SOUL.shX;
    c.save(); c.scale(1.12, 1);
    c.beginPath();
    c.moveTo(-5, -47); c.quadraticCurveTo(-12, -46, -sx - 3, -42.5); c.quadraticCurveTo(-sx - 5, -40, -16.5, -33);
    c.quadraticCurveTo(-15, -22, -11.5, -12); c.quadraticCurveTo(-13, -5, -14.5, 0);
    c.quadraticCurveTo(0, 5, 14.5, 0); c.quadraticCurveTo(13, -5, 11.5, -12);
    c.quadraticCurveTo(15, -22, 16.5, -33); c.quadraticCurveTo(sx + 5, -40, sx + 3, -42.5); c.quadraticCurveTo(12, -46, 5, -47); c.closePath();
    var g = c.createLinearGradient(-sx, 0, sx, 0);
    g.addColorStop(0, sk.b); g.addColorStop(0.25, sk.a); g.addColorStop(0.5, sk.hi); g.addColorStop(0.75, sk.a); g.addColorStop(1, sk.b);
    c.fillStyle = g; c.fill(); c.lineWidth = 1.1; c.strokeStyle = 'rgba(14,6,9,.65)'; c.stroke();
    c.save(); c.clip();
    var up = c.createLinearGradient(0, -47, 0, 2);                       // cool from above, lava from below
    up.addColorStop(0, 'rgba(150,175,215,.16)'); up.addColorStop(0.45, 'rgba(0,0,0,0)'); up.addColorStop(0.75, 'rgba(10,3,5,.18)'); up.addColorStop(1, rimCol(L, 0.62));
    c.fillStyle = up; c.fillRect(-30, -48, 60, 52);
    // collarbones, sternum
    c.lineCap = 'round';
    [-1, 1].forEach(function (sd) {
      c.strokeStyle = 'rgba(14,6,9,.55)'; c.lineWidth = 2.2; c.beginPath(); c.moveTo(sd * 2.5, -43); c.quadraticCurveTo(sd * 9, -45.4, sd * 16.5, -42.2); c.stroke();
      c.strokeStyle = 'rgba(235,222,205,.34)'; c.lineWidth = 1.2; c.beginPath(); c.moveTo(sd * 2.5, -43.6); c.quadraticCurveTo(sd * 9, -46, sd * 16.5, -43); c.stroke();
      // ribs: curved bands, a dark gap above each and a lit lower edge
      for (var i = 0; i < 6; i++) {
        var ry = -37 + i * 4.4 + br, w = 14.5 - i * 0.9 + (i > 3 ? -1 : 0), dr = 4 + i * 0.4;
        c.strokeStyle = 'rgba(12,4,7,' + (0.36 - i * 0.025) + ')'; c.lineWidth = 2;
        c.beginPath(); c.moveTo(sd * 2.2, ry - 1); c.quadraticCurveTo(sd * w * 0.7, ry - 2.2, sd * w, ry + dr); c.stroke();
        c.strokeStyle = 'rgba(' + L.glow + ',' + (0.26 * L.rim + 0.05) + ')'; c.lineWidth = 1.1;
        c.beginPath(); c.moveTo(sd * 2.4, ry + 1.2); c.quadraticCurveTo(sd * w * 0.7, ry - 0.2, sd * w, ry + dr + 1.6); c.stroke();
        c.strokeStyle = 'rgba(235,222,205,.16)'; c.lineWidth = 0.8;
        c.beginPath(); c.moveTo(sd * 2.4, ry - 0.2); c.quadraticCurveTo(sd * w * 0.7, ry - 1.4, sd * w, ry + dr + 0.4); c.stroke();
      }
      c.fillStyle = 'rgba(12,4,7,.18)'; c.beginPath(); c.ellipse(sd * 9, -7, 4.6, 6, sd * 0.2, 0, TAU); c.fill();         // hollow flank
      c.strokeStyle = 'rgba(235,222,205,.22)'; c.lineWidth = 1.4; c.beginPath(); c.moveTo(sd * 11.4, -3.6); c.quadraticCurveTo(sd * 13.6, -5.5, sd * 12.4, -8); c.stroke();   // hip bone
    });
    c.strokeStyle = 'rgba(235,222,205,.3)'; c.lineWidth = 1.5; c.beginPath(); c.moveTo(0, -43); c.lineTo(0, -24); c.stroke();
    c.strokeStyle = 'rgba(12,4,7,.4)'; c.lineWidth = 1; c.beginPath(); c.moveTo(0.9, -43); c.lineTo(0.9, -24); c.stroke();
    c.fillStyle = 'rgba(12,4,7,.28)'; c.beginPath(); c.ellipse(0, -13, 5.8, 8, 0, 0, TAU); c.fill();                // sunken belly
    c.fillStyle = 'rgba(12,4,7,.45)'; c.beginPath(); c.arc(0, -10, 0.9, 0, TAU); c.fill();
    c.restore(); c.restore();
  }

  // a ragged loincloth with fold shading, a rope belt and torn strips that sway
  function loincloth(c, sk, L, t) {
    var sw = Math.sin(t / 700) * 1.6;
    c.save();
    c.beginPath(); c.moveTo(-16, -4); c.lineTo(16, -4); c.lineTo(17.5, 7); c.lineTo(14, 13 + sw * 0.3); c.lineTo(10.5, 9.5); c.lineTo(6, 16 + sw * 0.8); c.lineTo(1, 10.5); c.lineTo(-4, 15 + sw * 0.5); c.lineTo(-9, 10); c.lineTo(-13, 14 + sw * 0.3); c.lineTo(-17.5, 8); c.closePath();
    var g = c.createLinearGradient(-15, -4, 15, 14);
    g.addColorStop(0, '#8d806a'); g.addColorStop(0.5, '#6b5f4c'); g.addColorStop(1, '#3f362b');
    c.fillStyle = g; c.fill(); c.lineWidth = 1; c.strokeStyle = 'rgba(14,6,9,.7)'; c.stroke();
    c.clip();
    for (var i = 0; i < 6; i++) {                                         // folds fanning down from the belt
      var fx = -12 + i * 4.8;
      c.strokeStyle = 'rgba(15,9,8,.42)'; c.lineWidth = 1.5; c.beginPath(); c.moveTo(fx * 0.8, -3); c.quadraticCurveTo(fx + sw * 0.4, 6, fx * 1.1 + (i % 2 ? 1.5 : -1.5), 18); c.stroke();
      c.strokeStyle = 'rgba(225,205,175,.15)'; c.lineWidth = 1; c.beginPath(); c.moveTo(fx * 0.8 + 1.4, -3); c.quadraticCurveTo(fx + sw * 0.4 + 1.4, 6, fx * 1.1 + 1.4 + (i % 2 ? 1.5 : -1.5), 18); c.stroke();
    }
    var rim = c.createLinearGradient(0, -2, 0, 19); rim.addColorStop(0, 'rgba(0,0,0,0)'); rim.addColorStop(0.55, 'rgba(0,0,0,0.1)'); rim.addColorStop(1, rimCol(L, 0.7));
    c.fillStyle = rim; c.fillRect(-20, -4, 40, 26);
    c.restore();
    c.strokeStyle = '#5b4630'; c.lineWidth = 2.2; c.lineCap = 'round'; c.beginPath(); c.moveTo(-15, -3.4); c.quadraticCurveTo(0, 0.5, 15, -3.4); c.stroke();
    c.strokeStyle = 'rgba(210,175,120,.4)'; c.lineWidth = 0.8; c.beginPath(); c.moveTo(-15, -4.2); c.quadraticCurveTo(0, -0.3, 15, -4.2); c.stroke();
    c.strokeStyle = '#5b4630'; c.lineWidth = 1.8; c.beginPath(); c.moveTo(4, -1.6); c.lineTo(6, 6 + sw * 0.3); c.moveTo(6, -1.4); c.lineTo(9, 5); c.stroke();
  }

  /*
   * drawSoul(c, o): o = {
   *   x, y            pelvis, screen px          s       scale          rot, lean   body rotation (rad)
   *   hands[2], feet[2]   {x,y} screen targets (null = let it hang)      grip[2]  0..1
   *   look {x,y} -1..1   mood  'calm'|'strain'|'scared'|'angry'|'happy'|'dizzy'|'smug'|'ouch'   mouth 0..1
   *   sweat 0..1   blink 0..1   skin, gear   t (ms)    halo {dx, dy, rot, on}   flat (sat / lying)
   *   glow 'r,g,b' (the lava), rim 0..1 (how strong its light is up here)
   * }
   */
  function drawSoul(c, o) {
    var s = o.s || 1, sk = skinRamp(SKINS[((o.skin | 0) % SKINS.length + SKINS.length) % SKINS.length]);
    var gear = GEARS[((o.gear | 0) % GEARS.length + GEARS.length) % GEARS.length];
    var t = o.t || 0, lean = o.lean || 0, rot = o.rot || 0, tot = rot + lean * 0.5;
    var mood = o.mood || 'calm', eff = mood === 'strain' ? 1 : mood === 'angry' || mood === 'scared' || mood === 'ouch' ? 0.7 : mood === 'calm' ? 0.25 : 0.4;
    var L = { glow: o.glow || '255,96,28', rim: o.rim == null ? 0.7 : o.rim, rot: tot, eff: eff };
    c.save();
    c.translate(o.x, o.y); c.rotate(tot); c.scale(s, s);
    var cr = Math.cos(-tot), sr = Math.sin(-tot);
    function loc(p) { var dx = (p.x - o.x) / s, dy = (p.y - o.y) / s; return { x: dx * cr - dy * sr, y: dx * sr + dy * cr }; }
    var i, Lm;

    // ---- legs
    var hip = [{ x: -SOUL.hipX, y: -2 }, { x: SOUL.hipX, y: -2 }];
    for (i = 0; i < 2; i++) {
      var ft = o.feet && o.feet[i] ? loc(o.feet[i]) : { x: hip[i].x * 1.3 + (i ? 2 : -2), y: 58 + Math.sin(t / 380 + i * 2) * 1.5 };
      Lm = ik(hip[i].x, hip[i].y, ft.x, ft.y - 4, SOUL.l1, SOUL.l2, i ? 1 : -1);
      seg(c, hip[i].x, hip[i].y, Lm.ex, Lm.ey, 17, 11.4, sk, L, { bulge: 0.5 + 0.5 * eff });
      seg(c, Lm.ex, Lm.ey, Lm.hx, Lm.hy, 11.4, 6.6, sk, L, { tendons: eff * 0.6 });
      joint(c, Lm.ex, Lm.ey, 4.3, sk, L);
      var fa = Math.atan2(Lm.hy - Lm.ey, Lm.hx - Lm.ex) - Math.PI / 2;
      foot(c, Lm.hx, Lm.hy + 1, o.feet && o.feet[i] ? fa * 0.25 : fa * 0.5, sk, L, o.feet && o.feet[i] ? 0.9 : 0.4);
    }
    // ---- torso + cloth
    torso(c, sk, L, t);
    loincloth(c, sk, L, t);
    if (gear === 'tie') {
      var tg = c.createLinearGradient(-5, 0, 5, 0); tg.addColorStop(0, '#4a0f14'); tg.addColorStop(0.5, '#8c2a2c'); tg.addColorStop(1, '#3c0b10');
      c.fillStyle = tg; c.strokeStyle = 'rgba(14,6,9,.8)'; c.lineWidth = 1;
      c.beginPath(); c.moveTo(-2.6, -46); c.lineTo(2.6, -46); c.lineTo(2, -42); c.lineTo(4.6, -22); c.lineTo(1.5, -17); c.lineTo(0, -20); c.lineTo(-1.8, -15); c.lineTo(-4.4, -23); c.lineTo(-2, -42); c.closePath(); c.fill(); c.stroke();
    } else if (gear === 'scarf') {
      var sg = c.createLinearGradient(0, -48, 0, -38); sg.addColorStop(0, '#7a2a24'); sg.addColorStop(1, '#3f1412');
      c.fillStyle = sg; c.strokeStyle = 'rgba(14,6,9,.8)'; c.lineWidth = 1;
      c.beginPath(); c.moveTo(-14, -45); c.quadraticCurveTo(0, -38.5, 14, -45); c.lineTo(14, -39.5); c.quadraticCurveTo(0, -32, -14, -39.5); c.closePath(); c.fill(); c.stroke();
      c.beginPath(); c.moveTo(7, -37); c.lineTo(14, -20 + Math.sin(t / 260) * 2); c.lineTo(11, -17 + Math.sin(t / 260) * 2); c.lineTo(9, -22); c.lineTo(5, -34); c.closePath(); c.fill(); c.stroke();
    } else if (gear === 'bow') {
      c.fillStyle = '#26356a'; c.strokeStyle = 'rgba(14,6,9,.8)'; c.lineWidth = 1;
      c.beginPath(); c.moveTo(0, -46); c.lineTo(-8, -50); c.lineTo(-7, -42); c.closePath(); c.fill(); c.stroke();
      c.beginPath(); c.moveTo(0, -46); c.lineTo(8, -50); c.lineTo(7, -42.5); c.closePath(); c.fill(); c.stroke();
      c.fillStyle = '#34478c'; c.beginPath(); c.arc(0, -46, 2, 0, TAU); c.fill(); c.stroke();
    }
    // ---- arms (in front of the torso)
    var sh = [{ x: -SOUL.shX, y: SOUL.shY }, { x: SOUL.shX, y: SOUL.shY }];
    for (i = 0; i < 2; i++) {
      var hd = o.hands && o.hands[i] ? loc(o.hands[i]) : { x: sh[i].x * 1.5, y: sh[i].y + 46 };
      var ddx = hd.x - sh[i].x, ddy = hd.y - sh[i].y, dl = Math.sqrt(ddx * ddx + ddy * ddy) || 1, reach = Math.min(10, dl * 0.25);      // the hand extends past the wrist
      Lm = ik(sh[i].x, sh[i].y, hd.x - ddx / dl * reach, hd.y - ddy / dl * reach, SOUL.a1, SOUL.a2, i ? 1 : -1);
      seg(c, sh[i].x, sh[i].y, Lm.ex, Lm.ey, 10.2, 7.8, sk, L, { bulge: 0.4 + 0.8 * eff });
      seg(c, Lm.ex, Lm.ey, Lm.hx, Lm.hy, 7.8, 5.4, sk, L, { tendons: 0.3 + 0.7 * eff });
      joint(c, Lm.ex, Lm.ey, 3.6, sk, L); joint(c, sh[i].x, sh[i].y, 6.2, sk, L, true);
      var ha = Math.atan2(Lm.hy - Lm.ey, Lm.hx - Lm.ex);
      hand(c, Lm.hx, Lm.hy, ha, o.grip ? o.grip[i] : 0.5, sk, L, i ? 1 : -1);
    }
    // ---- neck + head
    drawHead(c, o, sk, gear, t, L);
    c.restore();
  }

  function headPath(c, k) {
    k = k || 1;
    c.beginPath(); c.moveTo(0, -11.8 * k);
    c.bezierCurveTo(6.6, -11.8 * k, 10, -7, 9.9, -1.4); c.bezierCurveTo(9.7, 3.4, 7.8, 7.4, 4.7, 10);
    c.quadraticCurveTo(2.2, 12, 0, 12); c.quadraticCurveTo(-2.2, 12, -4.7, 10);
    c.bezierCurveTo(-7.8, 7.4, -9.7, 3.4, -9.9, -1.4); c.bezierCurveTo(-10, -7, -6.6, -11.8 * k, 0, -11.8 * k); c.closePath();
  }

  function drawHead(c, o, sk, gear, t, L) {
    var hy = SOUL.headY, look = o.look || { x: 0, y: -0.4 }, mood = o.mood || 'calm', eff = L.eff;
    var bob = Math.sin(t / 520) * 0.7, i;
    // the neck: cords that stand out when he strains
    var ng = c.createLinearGradient(-5, 0, 5, 0); ng.addColorStop(0, sk.b); ng.addColorStop(0.5, sk.a); ng.addColorStop(1, sk.b);
    c.fillStyle = ng; c.strokeStyle = 'rgba(14,6,9,.6)'; c.lineWidth = 1;
    c.beginPath(); c.moveTo(-4.2, hy + 8); c.lineTo(-5.2, -45); c.lineTo(5.2, -45); c.lineTo(4.2, hy + 8); c.closePath(); c.fill(); c.stroke();
    c.strokeStyle = 'rgba(14,6,9,' + (0.25 + 0.35 * eff) + ')'; c.lineWidth = 1.1; c.lineCap = 'round';
    [-1, 1].forEach(function (sd) { c.beginPath(); c.moveTo(sd * 3.8, hy + 9); c.quadraticCurveTo(sd * 3, -52, sd * 1, -44.5); c.stroke(); });
    c.fillStyle = 'rgba(12,4,7,.35)'; c.beginPath(); c.ellipse(0, -52, 4.6, 5, 0, 0, TAU); c.fill();
    c.save(); c.translate(0, hy + bob); c.scale(1.2, 1.2);
    if (o.headTilt) c.rotate(o.headTilt);
    // hair: a few lank strands
    c.strokeStyle = 'rgba(24,16,16,.9)'; c.lineWidth = 1.1;
    for (i = 0; i < 8; i++) { var hx = -8 + i * 2.3, sw = Math.sin(t / 600 + i) * 1.2; c.beginPath(); c.moveTo(hx * 0.8, -10.5); c.quadraticCurveTo(hx * 1.3, -13 + sw, hx * 1.5 + (i < 4 ? -2 : 2), -5 + (i % 3) * 2.5); c.stroke(); }
    // ears
    [-1, 1].forEach(function (sd) {
      c.fillStyle = sk.a; c.strokeStyle = 'rgba(14,6,9,.6)'; c.lineWidth = 1; c.beginPath(); c.ellipse(sd * 9.8, 0.4, 1.9, 3.3, sd * 0.15, 0, TAU); c.fill(); c.stroke();
    });
    // the skull-like head, lit from below by lava and from above by the cold
    headPath(c);
    var hg = c.createRadialGradient(-3, -6, 1, 0, 0, 14); hg.addColorStop(0, sk.hi); hg.addColorStop(0.5, sk.a); hg.addColorStop(1, sk.b);
    c.fillStyle = hg; c.fill(); c.lineWidth = 1.2; c.strokeStyle = 'rgba(14,6,9,.7)'; c.stroke();
    c.save(); headPath(c); c.clip();
    var lg = c.createLinearGradient(0, -12, 0, 12); lg.addColorStop(0, 'rgba(0,0,0,.08)'); lg.addColorStop(0.55, 'rgba(0,0,0,0)'); lg.addColorStop(1, rimCol(L, 0.7));
    c.fillStyle = lg; c.fillRect(-12, -12, 24, 24);
    // brow ridge, sockets, hollow cheeks
    c.fillStyle = 'rgba(12,4,7,.26)'; c.beginPath(); c.ellipse(0, -5.4, 8.6, 2.4, 0, 0, TAU); c.fill();
    var hollow = 0.2 + 0.18 * eff;
    [-1, 1].forEach(function (sd) {
      c.fillStyle = 'rgba(12,4,7,.5)'; c.beginPath(); c.ellipse(sd * 4.3, -2.2, 4, 3.3, sd * 0.1, 0, TAU); c.fill();
      c.fillStyle = 'rgba(12,4,7,' + hollow + ')'; c.beginPath(); c.ellipse(sd * 6.3, 4.8, 2.6, 3.6, -sd * 0.2, 0, TAU); c.fill();
      c.strokeStyle = 'rgba(235,222,205,.18)'; c.lineWidth = 1; c.beginPath(); c.arc(sd * 7.6, 1.2, 3.6, sd > 0 ? -0.4 : Math.PI - 1.1, sd > 0 ? 1.1 : Math.PI + 0.4); c.stroke();     // cheekbone light
    });
    if (eff > 0.6) {                                                      // veins at the temple, a furrowed brow
      c.strokeStyle = 'rgba(70,40,60,.45)'; c.lineWidth = 0.8;
      [-1, 1].forEach(function (sd) { c.beginPath(); c.moveTo(sd * 8.2, -7.5); c.quadraticCurveTo(sd * 6.6, -9.4, sd * 5.2, -10.6); c.moveTo(sd * 8, -5.8); c.lineTo(sd * 6, -8.4); c.stroke(); });
      c.strokeStyle = 'rgba(14,6,9,.3)'; c.lineWidth = 0.9; c.beginPath(); c.moveTo(-4, -8.6); c.lineTo(4, -8.6); c.moveTo(-3, -10); c.lineTo(3, -10); c.stroke();
    }
    c.restore();
    // eyes
    var blink = o.blink || 0, open = { calm: 1, strain: 0.55, scared: 1.35, angry: 0.7, happy: 0.75, dizzy: 1, smug: 0.55, ouch: 0.2 }[mood];
    if (open == null) open = 1;
    var oy = -2.3;
    for (i = 0; i < 2; i++) {
      var sd2 = i ? 1 : -1, ex = sd2 * 4.3, ry = 2.5 * open * (1 - blink * 0.92);
      c.save(); c.translate(ex, oy);
      if (mood === 'ouch' || ry < 0.5) {
        c.strokeStyle = 'rgba(14,6,9,.9)'; c.lineWidth = 1.5; c.beginPath(); c.moveTo(-2.6, 0); c.quadraticCurveTo(0, mood === 'ouch' ? -1.8 : 1.2, 2.6, 0); c.stroke();
      } else {
        c.fillStyle = '#d9d2c2'; c.strokeStyle = 'rgba(14,6,9,.85)'; c.lineWidth = 1;
        c.beginPath(); c.ellipse(0, 0, 2.9, ry, 0, 0, TAU); c.fill(); c.stroke();
        if (mood === 'dizzy') {
          c.strokeStyle = 'rgba(30,14,20,.9)'; c.lineWidth = 0.8; c.beginPath();
          for (var a = 0; a < 9; a += 0.4) c.lineTo(Math.cos(a + t / 160) * a * 0.3, Math.sin(a + t / 160) * a * 0.3); c.stroke();
        } else {
          var lx = clamp(look.x * 1.2, -1.3, 1.3), ly = clamp(look.y * 1, -1, 1) * Math.min(1, ry / 2);
          c.save(); c.beginPath(); c.ellipse(0, 0, 2.9, ry, 0, 0, TAU); c.clip();
          c.fillStyle = '#7f98ac'; c.beginPath(); c.arc(lx, ly, mood === 'scared' ? 1.5 : 1.8, 0, TAU); c.fill();
          c.fillStyle = '#10080a'; c.beginPath(); c.arc(lx, ly, mood === 'scared' ? 0.6 : 0.95, 0, TAU); c.fill();
          c.fillStyle = 'rgba(255,170,90,.9)'; c.beginPath(); c.arc(lx + 0.6, ly + 0.7, 0.45, 0, TAU); c.fill();           // the lava, reflected
          c.fillStyle = 'rgba(255,255,255,.9)'; c.beginPath(); c.arc(lx - 0.6, ly - 0.7, 0.4, 0, TAU); c.fill();
          c.restore();
        }
        c.strokeStyle = 'rgba(14,6,9,.9)'; c.lineWidth = 1.2; c.beginPath(); c.moveTo(-3, -ry * 0.1); c.quadraticCurveTo(0, -ry * 1.25, 3, -ry * 0.1); c.stroke();      // upper lid
        c.strokeStyle = 'rgba(20,8,12,.35)'; c.lineWidth = 0.8; c.beginPath(); c.moveTo(-2.8, ry + 0.8); c.quadraticCurveTo(0, ry + 1.8, 2.8, ry + 0.8); c.stroke();     // eye bag
      }
      c.restore();
    }
    // brows (the inner end lower = angry / straining, higher = worried)
    var br = { calm: [0.05, 0], strain: [0.5, 0.6], scared: [-0.55, -2.4], angry: [0.8, 1], happy: [-0.12, -1.3], dizzy: [0.1, 0], smug: [0.3, 0.2], ouch: [-0.4, -0.8] }[mood] || [0, 0];
    c.strokeStyle = 'rgba(22,12,12,.92)'; c.lineCap = 'round';
    [-1, 1].forEach(function (sd) {
      var by = -7.4 + br[1];
      c.lineWidth = 1.7; c.beginPath(); c.moveTo(sd * 0.9, by + br[0] * 3); c.quadraticCurveTo(sd * 4.6, by - 1.2 - br[0] * 0.5, sd * 8.2, by - br[0] * 2); c.stroke();
    });
    // nose
    c.strokeStyle = 'rgba(14,6,9,.4)'; c.lineWidth = 0.9; c.beginPath(); c.moveTo(-0.5, 0); c.quadraticCurveTo(-1, 3, -1.6, 4.2); c.stroke();
    c.beginPath(); c.moveTo(-2.2, 4.8); c.quadraticCurveTo(0, 5.8, 2.2, 4.8); c.stroke();
    c.strokeStyle = 'rgba(235,222,205,.22)'; c.beginPath(); c.moveTo(0.7, 0); c.lineTo(0.8, 4); c.stroke();
    // mouth
    var m = o.mouth || 0, my = 8, tw = 'rgba(14,6,9,.92)';
    c.lineWidth = 1.2; c.strokeStyle = tw; c.fillStyle = '#240a0e';
    function teeth(w, h) {
      c.save(); c.beginPath(); c.rect(-w, -h, w * 2, h); c.clip(); c.fillStyle = '#cfc7b4';
      c.fillRect(-w, -h, w * 2, h * 0.5); c.strokeStyle = 'rgba(14,6,9,.5)'; c.lineWidth = 0.6;
      for (var q = -w + 1.6; q < w; q += 1.7) { c.beginPath(); c.moveTo(q, -h); c.lineTo(q, 0); c.stroke(); }
      c.restore();
    }
    if (mood === 'happy') {
      c.beginPath(); c.moveTo(-4.4, my - 1.2); c.quadraticCurveTo(0, my + 3.4 + m * 2.4, 4.4, my - 1.2); c.quadraticCurveTo(0, my - 0.2, -4.4, my - 1.2); c.closePath(); c.fill(); c.stroke();
      c.save(); c.translate(0, my - 0.6); teeth(3.6, 1.4); c.restore();
    } else if (mood === 'scared' || mood === 'ouch') {
      c.beginPath(); c.ellipse(0, my + 1.2, 3 + m * 0.9, 2.6 + m * 2.2, 0, 0, TAU); c.fill(); c.stroke();
      c.save(); c.translate(0, my - 0.8 - m * 0.4); teeth(2.4, 1.2); c.restore();
      c.fillStyle = '#6a2230'; c.beginPath(); c.ellipse(0, my + 2.8 + m * 1.6, 1.8, 0.9 + m * 0.5, 0, 0, TAU); c.fill();
    } else if (mood === 'strain') {
      c.beginPath(); c.moveTo(-4.8, my - 1.4); c.quadraticCurveTo(0, my - 2.4, 4.8, my - 1.4); c.lineTo(4.2, my + 1.6 + m * 1.8); c.quadraticCurveTo(0, my + 2.6 + m * 2.2, -4.2, my + 1.6 + m * 1.8); c.closePath(); c.fill(); c.stroke();
      c.save(); c.translate(0, my + 0.4); teeth(4, 2.2 + m * 1.2); c.restore();
      c.strokeStyle = 'rgba(14,6,9,.5)'; c.lineWidth = 0.9; c.beginPath(); c.moveTo(-6.4, my - 2.4); c.quadraticCurveTo(-7.4, my, -6.2, my + 2.6); c.moveTo(6.4, my - 2.4); c.quadraticCurveTo(7.4, my, 6.2, my + 2.6); c.stroke();
    } else if (mood === 'angry') {
      c.beginPath(); c.moveTo(-4.4, my + 1.8); c.quadraticCurveTo(0, my - 1.6, 4.4, my + 1.8); c.stroke();
      c.save(); c.translate(0, my + 0.2); c.beginPath(); c.moveTo(-3, 0.4); c.lineTo(3, 0.4); c.lineTo(2.4, 1.8); c.lineTo(-2.4, 1.8); c.closePath(); c.fillStyle = '#cfc7b4'; c.fill(); c.stroke(); c.restore();
    } else if (mood === 'smug') {
      c.beginPath(); c.moveTo(-4, my + 0.8); c.quadraticCurveTo(1, my + 2.6, 4.6, my - 1); c.stroke();
    } else if (mood === 'dizzy') {
      c.beginPath(); c.moveTo(-4, my + 1.2); for (var z = 1; z <= 6; z++) c.lineTo(-4 + z * 1.35, my + 1.2 + (z % 2 ? -1.2 : 1.2)); c.stroke();
      c.fillStyle = '#a04a5a'; c.beginPath(); c.ellipse(1, my + 3, 1.6, 2, 0.2, 0, TAU); c.fill(); c.stroke();
    } else {
      c.beginPath(); c.moveTo(-3.8, my); c.quadraticCurveTo(0, my + 0.8 + m * 2.4, 3.8, my); c.stroke();
      c.strokeStyle = 'rgba(14,6,9,.28)'; c.lineWidth = 0.8; c.beginPath(); c.moveTo(-4.8, my - 1.2); c.quadraticCurveTo(-5.6, my + 0.4, -4.4, my + 1.8); c.moveTo(4.8, my - 1.2); c.quadraticCurveTo(5.6, my + 0.4, 4.4, my + 1.8); c.stroke();
    }
    c.strokeStyle = 'rgba(' + L.glow + ',' + (0.3 * L.rim) + ')'; c.lineWidth = 1; c.beginPath(); c.moveTo(-3.2, my + 4.6); c.quadraticCurveTo(0, my + 5.6, 3.2, my + 4.6); c.stroke();     // lit lower lip / chin
    // gear on the head
    if (gear === 'hardhat') {
      var hg2 = c.createLinearGradient(0, -20, 0, -8); hg2.addColorStop(0, '#d9a42a'); hg2.addColorStop(1, '#7a5612');
      c.fillStyle = hg2; c.strokeStyle = 'rgba(14,6,9,.85)'; c.lineWidth = 1.2;
      c.beginPath(); c.moveTo(-10.6, -6.4); c.quadraticCurveTo(-10, -17.5, 0, -18); c.quadraticCurveTo(10, -17.5, 10.6, -6.4); c.closePath(); c.fill(); c.stroke();
      c.fillStyle = '#6a4a10'; c.beginPath(); c.rect(-12.4, -7.2, 24.8, 2.8); c.fill(); c.stroke();
      c.fillStyle = 'rgba(255,240,200,.25)'; c.beginPath(); c.ellipse(-3.5, -14, 3, 1.6, -0.4, 0, TAU); c.fill();
      c.strokeStyle = 'rgba(40,20,5,.7)'; c.lineWidth = 0.9; c.beginPath(); c.moveTo(4, -17); c.lineTo(5.6, -13); c.lineTo(4.4, -10); c.stroke();           // a dent
      c.fillStyle = '#fff2c0'; c.beginPath(); c.arc(0, -13, 2, 0, TAU); c.fill(); c.stroke();
    } else if (gear === 'party') {
      var pg = c.createLinearGradient(-8, 0, 8, 0); pg.addColorStop(0, '#5a1f5c'); pg.addColorStop(0.5, '#8a3a86'); pg.addColorStop(1, '#4a1a4c');
      c.fillStyle = pg; c.strokeStyle = 'rgba(14,6,9,.85)'; c.lineWidth = 1.2;
      c.beginPath(); c.moveTo(-7, -9); c.lineTo(2.4, -26); c.lineTo(8, -9); c.closePath(); c.fill(); c.stroke();
      c.strokeStyle = 'rgba(210,190,120,.7)'; c.lineWidth = 1.2; c.beginPath(); c.moveTo(-4.4, -13); c.lineTo(5.4, -14.6); c.stroke();
      c.fillStyle = '#b8a060'; c.beginPath(); c.arc(2.4, -26.6, 1.6, 0, TAU); c.fill();
    } else if (gear === 'headband') {
      c.fillStyle = '#7d1f26'; c.strokeStyle = 'rgba(14,6,9,.85)'; c.lineWidth = 1;
      c.beginPath(); c.moveTo(-10.2, -5.4); c.quadraticCurveTo(0, -9.6, 10.2, -5.4); c.lineTo(10.2, -2.4); c.quadraticCurveTo(0, -6.6, -10.2, -2.4); c.closePath(); c.fill(); c.stroke();
      c.beginPath(); c.moveTo(10, -4); c.lineTo(18, -1 + Math.sin(t / 200) * 2); c.lineTo(16.6, -6.6 + Math.sin(t / 230)); c.closePath(); c.fill(); c.stroke();
      c.beginPath(); c.moveTo(10, -3.4); c.lineTo(16, 3 + Math.sin(t / 190) * 2); c.lineTo(13, 3.4); c.closePath(); c.fill(); c.stroke();
    } else if (gear === 'glasses') {
      c.strokeStyle = 'rgba(90,70,60,.95)'; c.lineWidth = 1; c.fillStyle = 'rgba(200,225,255,.12)';
      c.beginPath(); c.arc(-4.4, oy, 4.6, 0, TAU); c.fill(); c.stroke(); c.beginPath(); c.arc(4.4, oy, 4.6, 0, TAU); c.fill(); c.stroke();
      c.beginPath(); c.moveTo(-0.2, oy - 0.4); c.lineTo(0.2, oy - 0.4); c.moveTo(-9, oy - 0.4); c.lineTo(-10.4, oy - 1); c.moveTo(9, oy - 0.4); c.lineTo(10.4, oy - 1); c.stroke();
      c.strokeStyle = 'rgba(255,255,255,.7)'; c.lineWidth = 0.6; c.beginPath(); c.moveTo(3, oy - 3.4); c.lineTo(5.2, oy - 0.2); c.lineTo(3.8, oy + 2.8); c.stroke();      // a crack
    }
    // the halo: a thin, tarnished, cracked ring that gives off a ghostly glow
    var hl = o.halo || { on: true, dx: 0, dy: 0, rot: 0 };
    if (hl.on !== false) {
      c.save(); c.translate(1 + (hl.dx || 0) * 0.5, -19 + (hl.dy || 0) * 0.5 + Math.sin(t / 700) * 1); c.rotate(-0.25 + (hl.rot || 0));
      var hgl = c.createRadialGradient(0, 0, 4, 0, 0, 20); hgl.addColorStop(0, 'rgba(255,225,140,.22)'); hgl.addColorStop(1, 'rgba(255,225,140,0)');
      c.fillStyle = hgl; c.fillRect(-22, -14, 44, 28);
      c.strokeStyle = 'rgba(40,24,8,.8)'; c.lineWidth = 3.4; c.beginPath(); c.ellipse(0, 0, 11.5, 3.6, 0, 0, TAU); c.stroke();
      c.strokeStyle = '#cdb46a'; c.lineWidth = 1.8; c.beginPath(); c.ellipse(0, 0, 11.5, 3.6, 0, 0.4, TAU + 0.1); c.stroke();
      c.strokeStyle = 'rgba(255,245,200,.7)'; c.lineWidth = 0.8; c.beginPath(); c.ellipse(0, -0.5, 11.5, 3.6, 0, 3.4, 5.6); c.stroke();
      c.restore();
    }
    // soot (a lava geyser went off in his face)
    if (o.soot > 0.02) {
      c.fillStyle = 'rgba(24,18,22,' + (0.62 * o.soot) + ')'; headPath(c); c.fill();
      c.fillStyle = 'rgba(225,220,210,' + (0.95 * o.soot) + ')';
      [-1, 1].forEach(function (sd) { c.beginPath(); c.ellipse(sd * 4.3, oy, 3, 2.9 * open, 0, 0, TAU); c.fill(); c.fillStyle = 'rgba(14,6,9,' + o.soot + ')'; c.beginPath(); c.arc(sd * 4.3, oy, 1.2, 0, TAU); c.fill(); c.fillStyle = 'rgba(225,220,210,' + (0.95 * o.soot) + ')'; });
    }
    // sweat: beads on the brow and flung drops
    if (o.sweat > 0.05) {
      var sw = o.sweat;
      for (i = 0; i < 4; i++) {
        var ph = ((t / 900) + i * 0.27) % 1, bx = [-7.5, 7.2, -3.4, 4.6][i], bY = [-7, -6, -10, -10.4][i] + ph * (i < 2 ? 9 : 2);
        c.fillStyle = 'rgba(190,225,255,' + (0.8 * (1 - ph) * sw + 0.1) + ')'; c.strokeStyle = 'rgba(40,70,110,.5)'; c.lineWidth = 0.6;
        c.beginPath(); c.ellipse(bx, bY, 0.9, 1.3 + ph * 0.8, 0, 0, TAU); c.fill(); c.stroke();
        c.fillStyle = 'rgba(255,255,255,.8)'; c.beginPath(); c.arc(bx - 0.3, bY - 0.4, 0.3, 0, TAU); c.fill();
      }
      for (i = 0; i < 3; i++) {                                          // drops flung off by the effort
        var fp = ((t / 620) + i * 0.34) % 1, sd3 = i % 2 ? 1 : -1;
        c.fillStyle = 'rgba(200,230,255,' + (0.85 * (1 - fp) * sw) + ')';
        c.beginPath(); c.ellipse(sd3 * (11 + fp * 14), -4 + fp * 22 - Math.sin(fp * Math.PI) * 8, 0.9, 1.4, sd3 * 0.5, 0, TAU); c.fill();
      }
    }
    c.restore();
  }

  // ==================================================================== colours
  var COLC = {};
  function rgb(h) {
    var c = COLC[h];
    if (c) return c;
    if (h.charAt(0) === 'r') {                                     // 'rgb(1,2,3)' / 'rgba(1,2,3,.4)'
      var m = h.match(/[\d.]+/g);
      return (COLC[h] = [+m[0], +m[1], +m[2]]);
    }
    var s = h.charAt(0) === '#' ? h.slice(1) : h;
    if (s.length === 3) s = s.charAt(0) + s.charAt(0) + s.charAt(1) + s.charAt(1) + s.charAt(2) + s.charAt(2);
    var n = parseInt(s, 16);
    return (COLC[h] = [n >> 16 & 255, n >> 8 & 255, n & 255]);
  }
  function mixc(a, b, t) {
    var x = rgb(a), y = rgb(b);
    return 'rgb(' + Math.round(lerp(x[0], y[0], t)) + ',' + Math.round(lerp(x[1], y[1], t)) + ',' + Math.round(lerp(x[2], y[2], t)) + ')';
  }
  function rgba(h, al) { var x = rgb(h); return 'rgba(' + x[0] + ',' + x[1] + ',' + x[2] + ',' + al + ')'; }
  function shade(h, k) { var x = rgb(h); return 'rgb(' + clamp(Math.round(x[0] * k), 0, 255) + ',' + clamp(Math.round(x[1] * k), 0, 255) + ',' + clamp(Math.round(x[2] * k), 0, 255) + ')'; }
  function mkCanvas(w, h) { var cv = root.document.createElement('canvas'); cv.width = Math.max(1, Math.ceil(w)); cv.height = Math.max(1, Math.ceil(h)); return cv; }

  // ==================================================================== the wall: routes + holds
  // Five kinds of wall, in stretches of 7-14 levels (script.routes). Each limb has a grid of holds
  // (y = ph + k * sp, world px); the soul's hands / feet snap to the nearest hold in reach, so what he
  // grips is exactly what is drawn.
  var GRID = {
    ledge:   { hl: [0, 56],  hr: [28, 56], fl: [0, 56], fr: [28, 56] },
    chimney: { hl: [10, 56], hr: [38, 56], fl: [0, 56], fr: [28, 56] },
    chains:  { hl: [0, 56],  hr: [28, 56], fl: [0, 56], fr: [28, 56] },
    rope:    { hl: [0, 56],  hr: [28, 56], fl: [0, 56], fr: [28, 56] },
    ribs:    { hl: [0, 56],  hr: [28, 56], fl: [0, 56], fr: [28, 56] }
  };
  function sway(kind, y, T) {
    if (kind === 'chains') return Math.sin(T / 950 + y / 240) * 5;
    if (kind === 'rope') return Math.sin(T / 1150 + y / 300) * 8 + Math.sin(T / 410 + y / 90) * 1.5;
    return 0;
  }
  function holdXY(kind, limb, k, T) {
    var g = GRID[kind] || GRID.ledge, gl = g[limb], y = gl[0] + k * gl[1];
    var left = limb.charAt(1) === 'l', hand = limb.charAt(0) === 'h', n = hash2(k, left ? 3 : 5), x;
    switch (kind) {
      case 'chimney': x = left ? -60 : 60; if (!hand) x = left ? -57 : 57; break;
      case 'chains': x = left ? -18 : 18; if (!hand) x = left ? -16 : 16; break;
      case 'rope': x = left ? -7 : 7; if (!hand) x = left ? -7 : 7; break;
      case 'ribs': x = left ? -42 : 42; if (!hand) x = left ? -39 : 39; break;
      default: x = left ? -46 + n * 12 : 46 - n * 12; if (!hand) x = left ? -24 + n * 6 : 26 - n * 6;
    }
    return { x: x + sway(kind, y, T), y: y };
  }
  // the hold a limb is on, given the height it wants to be at (blended while it moves to the next hold)
  function snapHold(kind, limb, yT, T) {
    var g = (GRID[kind] || GRID.ledge)[limb], u = (yT - g[0]) / g[1], k = Math.floor(u), f = u - k, w = 0.42;
    var m = f > 1 - w ? smooth(1 - w, 1, f) : 0;
    var a = holdXY(kind, limb, k, T), b = holdXY(kind, limb, k + 1, T);
    return { x: lerp(a.x, b.x, m), y: lerp(a.y, b.y, m) + Math.sin(m * Math.PI) * 12 };
  }
  function routeKind(routes, level) {
    if (!routes || !routes.length) return 'ledge';
    for (var i = 0; i < routes.length; i++) if (level >= routes[i].from && level < routes[i].to) return routes[i].kind;
    return level < 0 ? 'ledge' : routes[routes.length - 1].kind;
  }
  function limbTargets(kind, py, pyAhead, T) {
    var hy = pyAhead + 35 + 28, fy = py - 26;
    return { hl: snapHold(kind, 'hl', hy, T), hr: snapHold(kind, 'hr', hy, T), fl: snapHold(kind, 'fl', fy, T), fr: snapHold(kind, 'fr', fy, T) };
  }

  // The soul hanging on the wall at (float) level lvl; lvlAhead = where he will be a moment later (the hands
  // reach before the body rises). xOff moves the whole thing sideways (the dead-end spur).
  function hangPose(routes, lvl, lvlAhead, T, xOff) {
    var py = lvl * LV + FOOT, pya = lvlAhead * LV + FOOT, f0 = Math.floor(lvl);
    var kA = routeKind(routes, f0), kB = routeKind(routes, f0 + 1), w = kA === kB ? 0 : smooth(0.35, 0.9, lvl - f0);
    var A = limbTargets(kA, py, pya, T), B = w ? limbTargets(kB, py, pya, T) : A;
    function mx(p, q) { return { x: lerp(p.x, q.x, w) + (xOff || 0), y: lerp(p.y, q.y, w) }; }
    var P = { hl: mx(A.hl, B.hl), hr: mx(A.hr, B.hr), fl: mx(A.fl, B.fl), fr: mx(A.fr, B.fr) };
    if (lvl < 1.3) { P.fl.y = Math.max(0, P.fl.y); P.fr.y = Math.max(0, P.fr.y); }       // the start platform
    var bx = (P.hl.x + P.hr.x) * 0.3 + (P.fl.x + P.fr.x) * 0.2;
    return { x: bx, y: py, hands: [P.hl, P.hr], feet: [P.fl, P.fr], kind: kA, lean: clamp((P.hr.y - P.hl.y) / 700, -0.08, 0.08), grip: [0.9, 0.9] };
  }

  // standing on the start platform
  function standPose(T) {
    var br = Math.sin(T / 640) * 1.4;
    return { x: 0, y: FOOT + br, hands: [{ x: -34, y: FOOT - 8 }, { x: 34, y: FOOT - 8 }], feet: [{ x: -15, y: 0 }, { x: 15, y: 0 }],
      kind: 'ledge', lean: 0, grip: [0.4, 0.4] };
  }

  // ==================================================================== the pose (every beat)
  // soulPose(tl, t, T): where he is and what his limbs, face and body are doing at script time t. T = a
  // free-running clock (ms) for the things that never stop (sway, breathing, blinking).
  function soulPose(tl, t, T) {
    var routes = tl.script.routes, i = tl.index(t), b = tl.beats[i];
    if (!b) return standPose(T);
    var u = tl.u(b, t), lvl = tl.level(t), ahead = tl.level(Math.min(tl.D, t + 150));
    var p, k;
    var ev = b.ev, bu = (t - b.t);
    switch (ev) {
      case 'ready': {
        p = standPose(T);
        var g = smooth(0.78, 1, u);                                // reaching for the first holds
        if (g > 0) {
          var h0 = hangPose(routes, 0, 0, T, 0);
          p.hands = [lerpPt(p.hands[0], h0.hands[0], g), lerpPt(p.hands[1], h0.hands[1], g)];
          p.grip = [lerp(0.4, 0.9, g), lerp(0.4, 0.9, g)];
        }
        var st = smooth(0.28, 0.5, u) * (1 - smooth(0.58, 0.72, u));   // a stretch
        p.hands = [lerpPt(p.hands[0], { x: -20, y: FOOT + 118 }, st), lerpPt(p.hands[1], { x: 20, y: FOOT + 118 }, st)];
        p.y += st * 6;
        p.mood = u < 0.28 ? 'calm' : u < 0.58 ? 'strain' : 'scared';
        p.look = u < 0.28 ? { x: Math.sin(u * 22) * 0.9, y: 0 } : { x: 0, y: -0.9 };
        p.mouth = st * 0.8 + (u > 0.6 ? 0.3 : 0);
        p.sweat = smooth(0.5, 0.8, u) * 0.7;
        p.line = 'ready';
        return p;
      }
      case 'climb': {
        p = hangPose(routes, lvl, ahead, T, 0);
        var eff = Math.sin(clamp((u - 0.2) / 0.8, 0, 1) * Math.PI);
        p.mood = 'strain'; p.mouth = 0.25 + eff * 0.6; p.look = { x: 0.2, y: -0.8 };
        p.sweat = clamp(tl.max ? lvl / Math.max(20, tl.max * 0.9) : 0.2, 0, 0.9) * 0.9;
        p.y += Math.sin(u * Math.PI) * -3;
        var par = (Math.floor(lvl + 0.001) & 1) ? 1 : -1, shift = Math.sin(clamp(u * 1.25, 0, 1) * Math.PI);       // weight over the planted side, then across
        p.x += par * shift * 3.2; p.lean = (p.lean || 0) + par * shift * 0.035;
        p.line = 'effort';
        return p;
      }
      case 'cheer': {
        p = hangPose(routes, lvl, lvl, T, 0);
        var pump = Math.abs(Math.sin(u * Math.PI * 2.2));
        p.hands[1] = { x: p.x + 24, y: p.y + 92 + pump * 38 };
        p.grip = [0.95, 1];
        p.mood = 'happy'; p.mouth = 0.8; p.look = { x: 0.2, y: -0.3 }; p.sweat = 0.2; p.y += pump * 4;
        p.line = 'cheer';
        return p;
      }
      case 'idle': return idlePose(tl, b, u, lvl, T);
      default: break;
    }
    // everything else (rest, taunt, slip, bat, geyser, deadend, grab, fray, fatal, fall, escape) is in the events part
    return eventPose(tl, b, u, lvl, ahead, T, t);
  }
  function lerpPt(a, b, t) { return { x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t) }; }

  function idlePose(tl, b, u, lvl, T) {
    var p = hangPose(tl.script.routes, lvl, lvl, T, 0), py = p.y, px = p.x, e = Math.sin(u * Math.PI);
    p.mood = 'calm'; p.mouth = 0.2; p.look = { x: 0, y: -0.2 }; p.sweat = 0.35; p.line = 'idle_' + b.kind;
    switch (b.kind) {
      case 'shrug':
        p.hands[1] = { x: px + 44, y: py + 14 + e * 4 }; p.grip[1] = 0; p.mood = 'smug'; p.headTilt = 0.18 * e; p.look = { x: 0.8, y: -0.2 }; p.y += e * 3;
        break;
      case 'wipe':
        p.hands[1] = { x: px + 15 + Math.sin(T / 90) * 5, y: py + 80 + e * 6 }; p.grip[1] = 0.2; p.sweat = 1; p.mood = 'strain'; p.mouth = 0.3;
        break;
      case 'pant':
        p.y += Math.sin(T / 130) * 2.4; p.mood = 'strain'; p.mouth = 0.9; p.look = { x: 0, y: 0.6 }; p.sweat = 0.9; p.lean = 0.05;
        break;
      case 'gulp':
        p.mood = 'scared'; p.look = { x: 0.1, y: 1 }; p.mouth = 0.4; p.sweat = 0.9; p.y -= e * 2;
        break;
      case 'wave':
        p.hands[1] = { x: px + 40, y: py + 100 + Math.sin(T / 100) * 12 }; p.grip[1] = 0; p.mood = 'happy'; p.mouth = 0.6; p.look = { x: 0, y: 0.1 };
        break;
      case 'flex':
        p.hands[1] = { x: px + 38, y: py + 70 + e * 6 }; p.grip[1] = 1; p.mood = 'smug'; p.mouth = 0.2; p.look = { x: 0.3, y: 0 };
        break;
      default: break;
    }
    return p;
  }

  // ==================================================================== the events (poses)
  // Where things are in the scene (world px, y up, x from the climbing line)
  var PLAT = { x0: -300, x1: -24 };          // the start platform (its top is y = 0)
  var LAVA_Y = -54;                          // the lava surface
  var SPUR = [{ from: -10, to: 9999, kind: 'ledge' }];   // the dead end's own little ledges

  function flail(px, py, T, k) {
    // arms and legs flung about (a fall): k = how wildly
    k = k == null ? 1 : k;
    return {
      hands: [{ x: px - 34 + Math.sin(T / 70) * 26 * k, y: py + 62 + Math.cos(T / 55) * 30 * k }, { x: px + 34 + Math.cos(T / 75) * 26 * k, y: py + 62 + Math.sin(T / 60) * 30 * k }],
      feet: [{ x: px - 22 + Math.sin(T / 85) * 14 * k, y: py - 36 + Math.cos(T / 65) * 10 * k }, { x: px + 22 + Math.cos(T / 90) * 14 * k, y: py - 36 + Math.sin(T / 70) * 10 * k }]
    };
  }
  var FATAL_RELEASE = { rock: 0.62, hand: 0.82, rope: 0.58, chain: 0.58, rib: 0.58, bat: 0.78, geyser: 0.52, demon: 0.84, tired: 0.86 };
  var FALL_ARRIVE = 0.0;

  function eventPose(tl, b, u, lvl, ahead, T, t) {
    var routes = tl.script.routes, p, px, py, e = Math.sin(u * Math.PI), side = b.side || 1, k;
    switch (b.ev) {
      case 'rest': {
        p = hangPose(routes, lvl, lvl, T, 0); px = p.x; py = p.y;
        var sit = smooth(0.0, 0.16, u) * (1 - smooth(0.88, 1, u));
        if (b.kind === 'lean') {
          p.hands = [lerpPt(p.hands[0], { x: px + 5, y: py + 22 }, sit), lerpPt(p.hands[1], { x: px - 5, y: py + 20 }, sit)];
          p.grip = [lerp(0.9, 1, sit), 1]; p.y += -4 * sit; p.lean = 0.05 * sit;
        } else {
          p.y = lerp(py, lvl * LV + 17, sit);
          p.hands = [lerpPt(p.hands[0], { x: px - 30, y: lvl * LV + 3 }, sit), lerpPt(p.hands[1], { x: px + 30, y: lvl * LV + 3 }, sit)];
          p.feet = [lerpPt(p.feet[0], { x: px - 15 + Math.sin(T / 520) * 6, y: p.y - 38 }, sit), lerpPt(p.feet[1], { x: px + 17 + Math.cos(T / 470) * 6, y: p.y - 36 }, sit)];
          p.grip = [0.5, 0.5]; p.sit = sit;
        }
        var pant = Math.sin(T / 150);
        p.y += pant * 1.6 * sit;
        p.mood = u < 0.25 ? 'strain' : (u > 0.8 ? 'calm' : 'happy'); p.mouth = 0.5 + pant * 0.4; p.sweat = 0.8 * (1 - smooth(0.4, 1, u));
        p.look = { x: Math.sin(u * 5) * 0.8, y: u > 0.3 && u < 0.7 ? 0.2 : -0.3 }; p.line = 'rest';
        return p;
      }
      case 'taunt': {
        p = hangPose(routes, lvl, lvl, T, 0); px = p.x; py = p.y;
        var fist = smooth(0.4, 0.5, u) * (1 - smooth(0.88, 0.98, u)), fi = side > 0 ? 1 : 0;
        p.hands[fi] = lerpPt(p.hands[fi], { x: px + side * 36 + Math.sin(T / 60) * 4 * fist, y: py + 94 + Math.cos(T / 70) * 6 * fist }, fist);
        p.grip[fi] = lerp(0.9, 1, fist);
        p.mood = u < 0.28 ? 'scared' : 'angry'; p.mouth = u > 0.45 ? 0.5 + Math.sin(T / 75) * 0.4 : 0.1;
        p.look = { x: side * 0.9, y: -0.35 }; p.sweat = 0.4; p.headTilt = -0.08 * side * fist; p.line = 'taunt_reply';
        return p;
      }
      case 'slip': {
        p = hangPose(routes, lvl, lvl + 0.2, T, 0); px = p.x; py = p.y;
        var sl = u < 0.52 ? 1 : 1 - smooth(0.52, 0.92, u);       // the feet are off the wall while he slides
        p.feet = [lerpPt(p.feet[0], { x: px - 24 + Math.sin(T / 60) * 12, y: py - 36 + Math.cos(T / 50) * 8 }, sl),
                  lerpPt(p.feet[1], { x: px + 22 + Math.cos(T / 66) * 12, y: py - 34 + Math.sin(T / 55) * 8 }, sl)];
        p.grip = [1, 1]; p.mood = u < 0.9 ? 'scared' : 'strain'; p.mouth = 0.9; p.sweat = 1;
        var sw2 = u > 0.5 ? Math.sin((u - 0.5) * 17) * Math.exp(-(u - 0.5) * 4.2) : 0;                      // the dangle: a damped swing from the hands
        p.look = { x: 0, y: 0.5 }; p.lean = Math.sin(T / 45) * 0.03 * sl + sw2 * 0.22; p.line = 'slip';
        p.x += Math.sin(T / 40) * 1.2 * sl + sw2 * 9; p.y += -Math.abs(sw2) * 3;
        return p;
      }
      case 'bat': {
        p = hangPose(routes, lvl, lvl, T, 0); px = p.x; py = p.y;
        p.hands[1] = { x: px + 30 + Math.sin(T / 85) * 20, y: py + 96 + Math.cos(T / 66) * 14 }; p.grip[1] = 0;
        p.mood = u < 0.14 ? 'scared' : 'angry'; p.mouth = 0.5 + Math.abs(Math.sin(T / 120)) * 0.5; p.sweat = 0.7;
        p.look = { x: Math.sin(T / 170), y: -0.5 + Math.cos(T / 130) * 0.4 }; p.x += Math.sin(T / 50) * 1.2; p.line = 'bat';
        return p;
      }
      case 'geyser': {
        p = hangPose(routes, lvl, lvl, T, 0); px = p.x; py = p.y;
        var bl = smooth(0.22, 0.34, u) * (1 - smooth(0.74, 0.96, u));
        p.lean = -side * 0.17 * bl; p.x -= side * 7 * bl;
        p.feet = [lerpPt(p.feet[0], { x: p.feet[0].x + Math.sin(T / 55) * 10 * bl, y: p.feet[0].y + Math.cos(T / 60) * 8 * bl }, 1),
                  lerpPt(p.feet[1], { x: p.feet[1].x + Math.cos(T / 58) * 10 * bl, y: p.feet[1].y + Math.sin(T / 52) * 8 * bl }, 1)];
        p.mood = u < 0.22 ? 'ouch' : 'scared'; p.mouth = 0.4 + bl * 0.6; p.sweat = 1; p.look = { x: side, y: 0.2 };
        p.line = 'geyser'; p.soot = smooth(0.34, 0.42, u);
        return p;
      }
      case 'deadend': {
        var f = u < 0.04 ? 0 : 1;
        var off = side * 82 * smooth(0.03, 0.16, u) * (1 - smooth(0.82, 0.96, u));
        p = hangPose(SPUR, lvl, tl.level(Math.min(tl.D, t + 140)), T, off); px = p.x; py = p.y;
        var atTop = smooth(0.42, 0.5, u) * (1 - smooth(0.58, 0.66, u));
        p.mood = u < 0.42 ? 'strain' : u < 0.66 ? 'ouch' : 'smug'; p.mouth = 0.3 + atTop * 0.7; p.sweat = 0.7;
        p.look = u < 0.42 ? { x: side * 0.2, y: -0.8 } : u < 0.66 ? { x: 0, y: -0.5 } : { x: -side * 0.7, y: 0.3 };
        p.y += atTop * 7 * Math.abs(Math.sin(u * 60)); p.line = 'deadend';
        p.spur = { side: side, off: off };
        return p;
      }
      case 'grab': {
        p = hangPose(routes, lvl, lvl, T, 0); px = p.x; py = p.y;
        var hx0 = -62, hy0 = lvl * LV - 4;                           // the hand lives in a crack down on the left
        var reach = smooth(0.08, 0.28, u), snatch = reach * (1 - smooth(0.78, 0.9, u));
        var ank = { x: px - 22 + Math.sin(T / 50) * 3 * snatch, y: py - 38 - 8 * snatch };
        var hx = lerp(hx0, ank.x - 4, reach), hy = lerp(hy0, ank.y - 4, reach);
        p.feet[0] = lerpPt(p.feet[0], ank, snatch);
        p.feet[1] = lerpPt(p.feet[1], { x: px - 38 + Math.sin(T / 52) * 14, y: py - 52 + Math.cos(T / 47) * 10 }, snatch * smooth(0.3, 0.4, u));
        p.mood = u < 0.5 ? 'scared' : 'angry'; p.mouth = 0.7; p.sweat = 0.9; p.look = { x: -0.5, y: 0.7 }; p.lean = 0.05 * snatch;
        p.fx = { type: 'hand', x: hx, y: hy, reach: reach, held: snatch, gone: smooth(0.82, 0.95, u), a: Math.atan2(ank.y - hy0, ank.x - hx0) };
        p.line = 'grab';
        return p;
      }
      case 'fray': {
        var decay = u < 0.3 ? 0.5 : Math.exp(-(u - 0.3) * 3);
        p = hangPose(routes, lvl, lvl, T, Math.sin(T / 330) * 9 * decay); px = p.x; py = p.y;
        var tw = smooth(0.28, 0.33, u) * (1 - smooth(0.36, 0.5, u));
        p.y -= tw * 9;
        p.mood = u < 0.3 ? 'scared' : u < 0.6 ? 'ouch' : 'strain'; p.mouth = 0.5; p.sweat = 1; p.look = { x: 0, y: -1 }; p.lean = Math.sin(T / 330) * 0.05 * decay;
        p.fx = { type: 'fray', snap: smooth(0.3, 0.34, u) }; p.line = 'fray';
        return p;
      }
      case 'fatal': return fatalPose(tl, b, u, lvl, T);
      case 'fall': return fallPose(tl, b, u, lvl, T, t);
      case 'escape': return escapePose(tl, b, u, lvl, T);
      default: break;
    }
    return hangPose(routes, lvl, lvl, T, 0);
  }

  // The last thing that happens before he falls: the cause. He loses his grip in a way that fits (the script says
  // which). At the end he hangs in the air for a moment (cartoon rules) - then the fall begins.
  function fatalPose(tl, b, u, lvl, T) {
    var routes = tl.script.routes, cause = b.cause || 'tired', rel = FATAL_RELEASE[cause] || 0.6;
    var p = hangPose(routes, lvl, lvl, T, 0), px = p.x, py = p.y;
    var gone = smooth(rel, rel + 0.12, u), pre = 1 - gone, trem = smooth(0.1, 0.5, u) * pre;
    var lose = p.hands.slice(), lf = p.feet.slice(), side = (b.s & 1) ? 1 : -1;
    var air = flail(px, py, T, 0.5 + gone * 0.4);
    p.fx = { type: 'fatal', cause: cause, u: u, rel: rel, side: side };
    p.line = 'fatal_' + cause;
    p.sweat = 1; p.mood = u < rel ? 'scared' : 'ouch'; p.mouth = 0.4 + smooth(0.3, 0.7, u) * 0.5;
    p.look = { x: 0, y: u < rel ? -0.8 : 0.9 };
    p.x += Math.sin(T / 30) * 1.4 * trem;
    var handsUp = [{ x: px - 20, y: py + 84 + Math.sin(T / 60) * 4 }, { x: px + 20, y: py + 84 + Math.cos(T / 66) * 4 }];
    var hold = [lerpPt(lose[0], handsUp[0], gone * 0.0), lerpPt(lose[1], handsUp[1], gone * 0.0)];
    switch (cause) {
      case 'rock':
        // the ledge under his feet crumbles: the feet lose it, the hands try to hold
        p.feet = [lerpPt(lf[0], air.feet[0], smooth(rel - 0.02, rel + 0.06, u)), lerpPt(lf[1], air.feet[1], smooth(rel - 0.02, rel + 0.06, u))];
        p.hands = [lerpPt(lose[0], handsUp[0], gone), lerpPt(lose[1], handsUp[1], gone)];
        p.grip = [1 - gone * 0.7, 1 - gone * 0.7];
        break;
      case 'hand': {
        var grab = smooth(0.12, 0.34, u), yank = smooth(0.55, 0.8, u);
        var an = { x: px - 22 - 10 * yank, y: py - 38 - 28 * yank };
        p.feet[0] = lerpPt(lf[0], an, grab);
        p.feet[1] = lerpPt(lf[1], { x: px - 34 + Math.sin(T / 52) * 12, y: py - 50 + Math.cos(T / 47) * 8 }, grab * (1 - gone));
        p.hands = [lerpPt(lose[0], { x: px - 22, y: py + 80 }, gone), lerpPt(lose[1], { x: px + 22, y: py + 80 }, gone)];
        p.grip = [1 - gone * 0.8, 1 - gone * 0.8]; p.y -= 12 * yank; p.lean = 0.12 * yank;
        p.fx.hand = { x: lerp(-62, an.x - 4, grab), y: lerp(lvl * LV - 4, an.y - 4, grab), held: grab, yank: yank };
        break;
      }
      case 'rope': case 'chain': case 'rib': {
        // it breaks in his hands: he is left holding a piece
        var br = smooth(rel - 0.03, rel + 0.03, u);
        p.hands = [lerpPt(lose[0], handsUp[0], br), lerpPt(lose[1], handsUp[1], br)];
        p.feet = [lerpPt(lf[0], air.feet[0], br), lerpPt(lf[1], air.feet[1], br)];
        p.grip = [1, 1]; p.fx.break = br; p.fx.piece = { x: px, y: py + 98 };
        p.y -= gone * 4;
        break;
      }
      case 'bat':
        p.hands = [lerpPt(lose[0], handsUp[0], gone), lerpPt(lose[1], { x: px + 30 + Math.sin(T / 80) * 22, y: py + 96 + Math.cos(T / 66) * 14 }, smooth(0.15, 0.3, u) * (1 - gone) + gone * 0)];
        if (gone > 0) p.hands[1] = lerpPt(p.hands[1], handsUp[1], gone);
        p.feet = [lerpPt(lf[0], air.feet[0], gone), lerpPt(lf[1], air.feet[1], gone)];
        p.grip = [1 - gone * 0.8, 0.2]; p.mood = u < rel ? 'angry' : 'ouch';
        break;
      case 'geyser': {
        var bl = smooth(0.46, 0.56, u);
        p.x += -side * 46 * bl; p.y += 18 * bl; p.rot = -side * 0.9 * bl;
        p.hands = [lerpPt(lose[0], { x: p.x - 28, y: p.y + 70 }, bl), lerpPt(lose[1], { x: p.x + 28, y: p.y + 70 }, bl)];
        p.feet = [lerpPt(lf[0], air.feet[0], bl), lerpPt(lf[1], air.feet[1], bl)];
        p.grip = [1 - bl * 0.9, 1 - bl * 0.9]; p.soot = bl;
        break;
      }
      case 'demon': {
        // a demon pries his fingers off, one hand then the other
        var d1 = smooth(0.4, 0.5, u), d2 = smooth(0.62, 0.72, u), dm = smooth(0.14, 0.3, u);
        p.hands = [lerpPt(lose[0], { x: lose[0].x - 8, y: lose[0].y - 30 }, d1), lerpPt(lose[1], { x: lose[1].x + 8, y: lose[1].y - 30 }, d2)];
        p.grip = [1 - d1 * 0.85, 1 - d2 * 0.85];
        p.feet = [lerpPt(lf[0], air.feet[0], d2 * d1), lerpPt(lf[1], air.feet[1], d2 * d1)];
        p.y -= 10 * d1 * (1 - d2) + 22 * d2;
        p.fx.demon = { appear: dm, d1: d1, d2: d2 };
        break;
      }
      default: {                          // tired: the arms just give out
        var sag = smooth(0.2, rel, u);
        p.hands = [lerpPt(lose[0], { x: lose[0].x - 3, y: lose[0].y - 18 * sag }, 1), lerpPt(lose[1], { x: lose[1].x + 3, y: lose[1].y - 18 * sag }, 1)];
        p.grip = [1 - sag * 0.9, 1 - sag * 0.9]; p.y -= 14 * sag; p.x += Math.sin(T / 26) * 2.2 * sag * pre;
        p.feet = [lerpPt(lf[0], air.feet[0], gone), lerpPt(lf[1], air.feet[1], gone)];
        p.hands = [lerpPt(p.hands[0], handsUp[0], gone), lerpPt(p.hands[1], handsUp[1], gone)];
        p.mood = u < rel ? 'strain' : 'ouch';
      }
    }
    if (gone > 0.5) {                           // hanging in the air: a gulp, a little wave
      p.mood = 'scared'; p.mouth = 0.3; p.look = { x: 0, y: 1 };
    }
    p.hover = gone;
    return p;
  }

  // where a fall ends and what happens then (fractions of the fall beat)
  function fallTimes(style) {
    return { imp: FALL_IMPACT[style] || 0.55 };
  }

  // The fall. The pelvis path is tl.level (down the wall, faster and faster); this adds the way he tumbles and what
  // the style does to him at the bottom: bonks on the way, a splash, the cauldron, the demon's flick, the grinder ...
  function fallPose(tl, b, u, lvl, T, t) {
    var style = b.style || 'yelp', imp = fallTimes(style).imp, M = b.lv, p;
    var py = lvl * LV + FOOT, px = 0, post = tl.D - b.t;           // ms in the fall
    var ms = (t - b.t), d = b.d, after = Math.max(0, ms);
    var fl = flail(px, py, T, 1);
    p = { x: 0, y: py, hands: fl.hands, feet: fl.feet, lean: 0, grip: [0.9, 0.9], kind: 'ledge', mood: 'scared', mouth: 1, sweat: 1, look: { x: 0, y: 1 }, rot: 0 };
    p.fx = { type: 'fall', style: style, u: u, imp: imp };
    p.line = 'fall';
    var g = sat01(u / imp), tum = Math.sin(T / 260) * 0.5 + g * 0.8;
    p.rot = tum; p.halo = { on: true, dx: 0, dy: -22 * smooth(0, 0.15, u) - 6 * Math.sin(T / 140), rot: Math.sin(T / 200) * 0.5 };
    var post_u = sat01((u - imp) / (1 - imp));
    switch (style) {
      case 'bonk': return bonkPose(tl, b, u, lvl, T, p);
      case 'yelp': {
        p.x = Math.sin(u * 8) * 14;
        if (u > imp) {                                         // into the lava: a thumbs up, then gone
          var sink = ease(post_u);
          p.y = LAVA_Y + 26 - 150 * sink; p.rot = 0.2 * (1 - post_u); p.x = 6;
          p.hands = [{ x: p.x - 26, y: p.y + 42 }, { x: p.x + 14, y: LAVA_Y + 66 - 120 * smooth(0.5, 1, post_u) }];
          p.grip = [0.9, 2]; p.mood = post_u < 0.5 ? 'dizzy' : 'smug'; p.mouth = 0.3; p.feet = [{ x: p.x - 10, y: p.y - 30 }, { x: p.x + 12, y: p.y - 30 }];
          p.look = { x: 0, y: 0 }; p.lava = true; p.halo = { on: true, dx: 6, dy: -22 + 14 * post_u, rot: 0.4 * post_u };
          p.line = 'splash';
        }
        return p;
      }
      case 'cauldron': {
        p.x = 8 + (1 - g) * Math.sin(u * 8) * 14;
        if (u > imp) {
          var sq = Math.exp(-post_u * 5) * Math.sin(post_u * 26);
          p.y = CAULDRON.rim - 8 + sq * 14 + Math.sin(T / 430) * 2; p.rot = 0.12 * Math.sin(T / 600) * (1 - post_u * 0.5); p.x = 8;
          p.hands = [{ x: p.x - 44 + Math.sin(T / 300) * 6, y: CAULDRON.rim + 36 + Math.sin(T / 260) * 8 }, { x: p.x + 44, y: CAULDRON.rim + 28 + Math.cos(T / 250) * 8 }];
          p.feet = [{ x: p.x - 12, y: p.y - 30 }, { x: p.x + 12, y: p.y - 30 }]; p.grip = [0.2, 0.2];
          p.mood = post_u < 0.4 ? 'dizzy' : 'smug'; p.mouth = 0.4; p.look = { x: Math.sin(T / 700), y: 0 }; p.sweat = 0.4; p.cauldron = true;
          p.halo = { on: true, dx: 0, dy: -4 + Math.sin(T / 300) * 2, rot: 0.2 };
          p.line = 'splash';
        }
        return p;
      }
      case 'flick': return flickPose(tl, b, u, T, p);
      case 'grinder': return grinderPose(tl, b, u, T, p);
      case 'boing': return boingPose(tl, b, u, T, p);
      case 'umbrella': return umbrellaPose(tl, b, u, T, p);
      default: break;
    }
    return p;
  }

  // ---- the bonk fall: down the wall in n hops, off a ledge each time, and flat onto the platform
  function bonkSeg(b, u) {
    var imp = FALL_IMPACT.bonk, n = Math.max(1, b.n || 1), v = sat01(u / imp) * n, k = Math.min(n - 1, Math.floor(v));
    return { n: n, k: k, f: v - k };
  }
  function bonkPose(tl, b, u, lvl, T, p) {
    var imp = FALL_IMPACT.bonk, sg = bonkSeg(b, u), n = sg.n, last = sg.k === n - 1;
    var side = (sg.k % 2 ? 1 : -1);
    var xTo = last ? -118 : side * -34, xFrom = sg.k === 0 ? 0 : (sg.k % 2 ? -1 : 1) * 34 * -1 * -1;
    xFrom = sg.k === 0 ? 0 : ((sg.k - 1) % 2 ? 34 : -34);
    if (last) xTo = -128;
    p.x = lerp(xFrom, xTo, ease(sg.f));
    var ph = u <= imp ? sg.f : 1;
    p.rot = u <= imp ? Math.sin(T / 140) * 0.6 + sg.k * 1.5 + sg.f * 3.2 * (sg.k % 2 ? 1 : -1) : 0;
    p.fx.bonk = { k: sg.k, f: sg.f, n: n };
    if (u > imp) {                                        // lying flat on the platform, seeing stars
      var lie = smooth(imp, imp + 0.06, u);
      p.x = -128; p.y = lerp(p.y, 10, lie); p.rot = lerp(p.rot, 1.52, lie) + Math.sin(T / 260) * 0.02;
      p.hands = [{ x: p.x - 30, y: 6 + Math.sin(T / 400) * 3 }, { x: p.x - 8, y: -2 }];
      p.feet = [{ x: p.x + 38, y: 4 }, { x: p.x + 40, y: -2 + Math.sin(T / 300) * 3 }];
      p.grip = [0.3, 0.3]; p.mood = 'dizzy'; p.mouth = 0.2; p.sweat = 0; p.look = { x: 0, y: 0 };
      p.halo = { on: true, dx: 0, dy: -4 + Math.sin(T / 160) * 3, rot: Math.sin(T / 130) * 0.6 };
      p.line = 'bonk'; p.fx.stars = lie;
    }
    return p;
  }

  // ---- the demon's flick, the grinder, the trampoline and the umbrella (their props are in the scene part)
  var DEMON = { x: 78, handY: -8 };                    // where the big hand waits (world)
  var CAULDRON = { x: 8, rim: LAVA_Y + 38 };
  var GRIND = { x: 20, hopperY: 14 };

  function flickPose(tl, b, u, T, p) {
    var imp = FALL_IMPACT.flick, post = sat01((u - imp) / (1 - imp)), hold = smooth(0, 0.06, post) * (1 - smooth(0.22, 0.26, post));
    var flick = smooth(0.24, 0.44, post);
    if (u <= imp) { p.x = 4 + (1 - sat01(u / imp)) * Math.sin(u * 9) * 12; return p; }
    // pinched between a finger and a thumb, kicking; then FLICK
    var hx = DEMON.x - 38, hy = DEMON.handY + 18;
    if (post < 0.24) {
      p.x = hx + Math.sin(T / 40) * 2; p.y = hy - 6 + Math.sin(T / 55) * 3; p.rot = 0.1;
      p.hands = [{ x: p.x - 34 + Math.sin(T / 60) * 8, y: p.y + 30 }, { x: p.x + 30, y: p.y + 40 + Math.cos(T / 60) * 8 }];
      p.feet = [{ x: p.x - 14 + Math.sin(T / 50) * 10, y: p.y - 36 }, { x: p.x + 14 + Math.cos(T / 44) * 10, y: p.y - 36 }];
      p.mood = 'ouch'; p.mouth = 0.6; p.look = { x: 1, y: 0 }; p.grip = [0.2, 0.2];
    } else {
      var s = flick;                                                   // the soul rockets away to the right
      var tt = (post - 0.24) * b.d / 1000;
      p.x = hx + tt * 900; p.y = hy + tt * 380 - 300 * tt * tt; p.rot = tt * 22;
      p.hands = [{ x: p.x - 24, y: p.y + 40 }, { x: p.x + 24, y: p.y + 40 }]; p.feet = [{ x: p.x - 14, y: p.y - 34 }, { x: p.x + 14, y: p.y - 34 }];
      p.mood = 'scared'; p.mouth = 1; p.grip = [0.9, 0.9]; p.look = { x: 0, y: 0 };
      p.gone = tt > 1.25;
    }
    p.fx.flick = { post: post, hold: hold, flick: flick }; p.line = 'flick';
    return p;
  }

  function grinderPose(tl, b, u, T, p) {
    var imp = FALL_IMPACT.grinder, post = sat01((u - imp) / (1 - imp));
    if (u <= imp) { p.x = GRIND.x + (1 - sat01(u / imp)) * Math.sin(u * 9) * 14; return p; }
    // into the hopper, round the wheel, out as a sausage link
    p.fx.grind = { post: post };
    p.x = GRIND.x; p.y = GRIND.hopperY + 40 - 130 * smooth(0, 0.18, post); p.rot = 0;
    p.hands = [{ x: p.x - 26, y: p.y + 60 }, { x: p.x + 26, y: p.y + 60 }]; p.feet = [{ x: p.x - 14, y: p.y - 34 }, { x: p.x + 14, y: p.y - 34 }];
    p.mood = 'scared'; p.mouth = 1; p.line = 'grinder'; p.hidden = post > 0.16;
    return p;
  }

  function boingPose(tl, b, u, T, p) {
    var imp = FALL_IMPACT.boing, post = sat01((u - imp) / (1 - imp)), cap = Math.max(0.2, Math.min(1.8, b.lv * 0.5)) * LV;
    if (u <= imp) { p.x = 6 + (1 - sat01(u / imp)) * Math.sin(u * 9) * 12; return p; }
    // three bounces on the trampoline, each lower, then a flop
    var n = 3, v = Math.min(n - 1e-6, post * 3.2), k = Math.floor(v), f = v - k, h = cap * Math.pow(0.55, k) * 1.6;
    var arc = post < 3 / 3.2 ? Math.sin(f * Math.PI) * h : 0;
    p.x = 6 + k * 3; p.y = TRAMP.y + FOOT * 0.7 + arc; p.rot = post < 0.94 ? (k % 2 ? -1 : 1) * Math.sin(f * Math.PI) * 1.4 : 0;
    p.hands = [{ x: p.x - 36, y: p.y + 50 + Math.sin(T / 60) * 12 }, { x: p.x + 36, y: p.y + 50 + Math.cos(T / 60) * 12 }];
    p.feet = [{ x: p.x - 16, y: p.y - 34 }, { x: p.x + 16, y: p.y - 34 }];
    p.mood = post < 0.6 ? 'scared' : 'dizzy'; p.mouth = post < 0.6 ? 1 : 0.2; p.grip = [0.9, 0.9];
    p.fx.boing = { post: post, k: k, f: f }; p.line = 'boing';
    if (post > 0.94) { p.y = TRAMP.y + 14; p.rot = 1.5; p.x = 6; p.mood = 'dizzy'; }
    return p;
  }
  var TRAMP = { x: 6, y: LAVA_Y + 40 };

  function umbrellaPose(tl, b, u, T, p) {
    var imp = FALL_IMPACT.umbrella, open = smooth(0.05, 0.14, u), post = sat01((u - imp) / (1 - imp));
    var g = sat01(u / imp), burn = smooth(0.5, 0.74, u);
    p.x = Math.sin(T / 640) * 20 * open * (1 - burn); p.rot = Math.sin(T / 640) * 0.18 * open * (1 - burn) * -1 + (burn > 0 ? Math.sin(T / 60) * 0.1 * burn : 0);
    // the right hand holds the shaft (at his side, chest high); the left one waves at whoever is down there
    var wave = Math.sin(T / 170);
    p.hands = [{ x: p.x - 40 + wave * 6, y: p.y + 66 + Math.cos(T / 170) * 10 }, { x: p.x + 31, y: p.y + 50 }]; p.grip = [0.1, 1];
    p.feet = [{ x: p.x - 10, y: p.y - 38 + Math.sin(T / 300) * 4 }, { x: p.x + 11, y: p.y - 36 + Math.cos(T / 300) * 4 }];
    p.mood = u < 0.14 ? 'scared' : burn < 0.1 ? 'happy' : 'scared'; p.mouth = burn > 0.1 ? 1 : 0.4; p.sweat = burn;
    p.fx.umb = { open: open, burn: burn, u: u }; p.line = 'umbrella';
    if (u > imp) {
      p.x = 0; p.y = lerp(p.y, 12 + FOOT, smooth(0, 0.1, post)); p.rot = 0; p.mood = 'dizzy'; p.soot = 1;
      p.hands = [{ x: p.x - 30, y: p.y + 6 }, { x: p.x + 30, y: p.y + 6 }]; p.feet = [{ x: p.x - 14, y: 2 }, { x: p.x + 14, y: 2 }];
      p.grip = [0.4, 0.4]; p.mouth = 0.2; p.sweat = 0; p.fx.umb.u = u;
    }
    return p;
  }

  // ---- over the lip: he did it
  function escapePose(tl, b, u, lvl, T) {
    var routes = tl.script.routes, p = hangPose(routes, lvl, lvl, T, 0), py = p.y;
    var over = smooth(0.1, 0.5, u), stand = smooth(0.5, 0.64, u), jump = Math.abs(Math.sin(smooth(0.62, 1, u) * Math.PI * 3));
    var rimY = (tl.H * LV) + 14;
    p.y = lerp(py, rimY + FOOT, over) + jump * 22 * stand;
    p.x = lerp(p.x, 18, over);
    var lift = over * (1 - stand);
    p.hands = [lerpPt(p.hands[0], { x: p.x - 36, y: rimY + 4 }, over * (1 - stand)), lerpPt(p.hands[1], { x: p.x + 30, y: rimY + 2 }, over * (1 - stand))];
    p.feet = [lerpPt(p.feet[0], { x: p.x - 12, y: rimY }, over), lerpPt(p.feet[1], { x: p.x + 14, y: rimY }, over)];
    // victory: both arms up, jumping
    var vic = smooth(0.58, 0.7, u);
    p.hands = [lerpPt(p.hands[0], { x: p.x - 38, y: p.y + 108 + Math.sin(T / 110) * 8 }, vic), lerpPt(p.hands[1], { x: p.x + 38, y: p.y + 108 + Math.cos(T / 110) * 8 }, vic)];
    p.grip = [lerp(0.9, 1, vic), lerp(0.9, 1, vic)];
    p.mood = stand > 0.2 ? 'happy' : 'strain'; p.mouth = 0.5 + vic * 0.5; p.look = { x: 0, y: -0.2 }; p.sweat = 0.5 * (1 - vic);
    p.tailWag = vic > 0.5; p.line = 'escape'; p.fx = { type: 'escape', u: u, vic: vic }; p.lean = Math.sin(T / 130) * 0.06 * vic;
    p.halo = { on: true, dx: 0, dy: -6 * vic, rot: Math.sin(T / 100) * 0.2 * vic };
    p.rimY = rimY;
    return p;
  }

  // ==================================================================== the world (drawn every frame)
  var CH = 480;                                  // a baked wall chunk is CH world px tall

  function lvlStratum(l) { return l < 6 ? -1 : Math.floor((l - 6) / 14); }
  function strataColor(th, lvl, H) {
    var s = lvlStratum(lvl), col = s < 0 ? mixc(th.lavaDeep, '#140809', 0.5) : th.strata[s % th.strata.length];
    var fromTop = H - lvl;
    if (fromTop < 18) col = mixc(col, th.skyTop, 0.3 * (1 - Math.max(0, fromTop) / 18));
    return col;
  }
  function strataEdge(s, x) { return 15 * Math.sin(x * 0.016 + s * 1.7) + 9 * Math.sin(x * 0.041 + s * 3.1) + 4 * Math.sin(x * 0.11 + s); }

  function blob(c, x, y, r, rnd, sx, sy) {
    var n = 8 + Math.floor(rnd() * 4), pts = [], i;
    for (i = 0; i < n; i++) { var a = i / n * TAU, rad = r * (0.74 + rnd() * 0.36); pts.push([x + Math.cos(a) * rad * (sx || 1.15), y + Math.sin(a) * rad * (sy || 0.86)]); }
    c.beginPath();
    var m0 = [(pts[n - 1][0] + pts[0][0]) / 2, (pts[n - 1][1] + pts[0][1]) / 2];
    c.moveTo(m0[0], m0[1]);
    for (i = 0; i < n; i++) { var p = pts[i], q = pts[(i + 1) % n]; c.quadraticCurveTo(p[0], p[1], (p[0] + q[0]) / 2, (p[1] + q[1]) / 2); }
    c.closePath();
  }

  function hexPath(c, x, y, r) {
    c.beginPath();
    for (var i = 0; i < 6; i++) { var a = i / 6 * TAU - Math.PI / 2; c.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r); }
    c.closePath();
  }

  function skull(c, x, y, r, th, rot) {
    c.save(); c.translate(x, y); c.rotate(rot || 0);
    var sg = c.createLinearGradient(0, -r, 0, r * 1.2); sg.addColorStop(0, shade(th.bone, 0.8)); sg.addColorStop(0.6, th.bone); sg.addColorStop(1, mixc(th.bone2, 'rgb(' + th.glow + ')', 0.35));
    c.fillStyle = sg; c.strokeStyle = 'rgba(30,10,10,.7)'; c.lineWidth = 1.4;
    c.beginPath(); c.ellipse(0, -r * 0.1, r, r * 0.92, 0, 0, TAU); c.fill(); c.stroke();
    c.beginPath(); rr(c, -r * 0.55, r * 0.55, r * 1.1, r * 0.62, r * 0.18); c.fill(); c.stroke();
    c.fillStyle = '#1b0a0c';
    c.beginPath(); c.ellipse(-r * 0.4, -r * 0.05, r * 0.28, r * 0.34, 0, 0, TAU); c.fill();
    c.beginPath(); c.ellipse(r * 0.4, -r * 0.05, r * 0.28, r * 0.34, 0, 0, TAU); c.fill();
    c.beginPath(); c.moveTo(0, r * 0.22); c.lineTo(-r * 0.12, r * 0.5); c.lineTo(r * 0.12, r * 0.5); c.closePath(); c.fill();
    c.strokeStyle = 'rgba(30,10,10,.7)'; c.lineWidth = 1.4;
    for (var i = -2; i <= 2; i++) { c.beginPath(); c.moveTo(i * r * 0.2, r * 0.62); c.lineTo(i * r * 0.2, r * 1.1); c.stroke(); }
    c.restore();
  }
  function femur(c, x, y, len, th, rot) {
    c.save(); c.translate(x, y); c.rotate(rot || 0);
    c.strokeStyle = 'rgba(30,10,10,.8)'; c.lineWidth = len * 0.14 + 3; c.lineCap = 'round';
    c.beginPath(); c.moveTo(-len / 2, 0); c.lineTo(len / 2, 0); c.stroke();
    c.strokeStyle = th.bone; c.lineWidth = len * 0.14; c.beginPath(); c.moveTo(-len / 2, 0); c.lineTo(len / 2, 0); c.stroke();
    [-1, 1].forEach(function (sd) {
      c.fillStyle = th.bone; c.strokeStyle = 'rgba(30,10,10,.8)'; c.lineWidth = 2;
      c.beginPath(); c.arc(sd * len / 2, -len * 0.07, len * 0.1, 0, TAU); c.arc(sd * len / 2, len * 0.07, len * 0.1, 0, TAU); c.fill();
    });
    c.restore();
  }
  function hexGem(c, x, y, r, col, rot) {
    c.save(); c.translate(x, y); c.rotate(rot || 0);
    c.beginPath(); for (var i = 0; i < 6; i++) { var a = i / 6 * TAU + TAU / 12; c.lineTo(Math.cos(a) * r, Math.sin(a) * r); } c.closePath();
    c.fillStyle = col; c.fill(); c.strokeStyle = 'rgba(0,0,0,.55)'; c.lineWidth = 2; c.stroke();
    c.strokeStyle = 'rgba(255,255,255,.55)'; c.lineWidth = 1.4;
    c.beginPath(); c.moveTo(-r * 0.5, -r * 0.1); c.lineTo(-r * 0.1, -r * 0.62); c.lineTo(r * 0.3, -r * 0.5); c.stroke();
    c.restore();
  }


  // ---- the rock: value noise in WORLD coordinates (so chunks join without a seam) and a per-pixel lit height field
  var NZ = (function () { var t = new Float32Array(512 * 512), r = mulberry(4242), i; for (i = 0; i < t.length; i++) t[i] = r(); return t; })();
  function vn(x, y) {
    var xi = Math.floor(x), yi = Math.floor(y), fx = x - xi, fy = y - yi, xj, yj;
    xi &= 511; yi &= 511; xj = (xi + 1) & 511; yj = (yi + 1) & 511;
    var a = NZ[yi * 512 + xi], b = NZ[yi * 512 + xj], c2 = NZ[yj * 512 + xi], d = NZ[yj * 512 + xj], u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
    return a + (b - a) * u + (c2 - a) * v + (a - b - c2 + d) * u * v;
  }
  function rockH(wx, wy) {
    var bed = (wy + 5 * Math.sin(wx * 0.013) + 3 * Math.sin(wx * 0.05 + 1) + 16 * vn(wx * 0.006, wy * 0.01)) / 52, fb = bed - Math.floor(bed);
    var slab = Math.pow(fb, 1.6) * 0.9 - (fb > 0.9 ? (fb - 0.9) * 8 : 0);                    // each bed leans out and ends in a lip
    var n = vn(wx * 0.011, wy * 0.011) * 0.9 + vn(wx * 0.034, wy * 0.034) * 0.42 + vn(wx * 0.09, wy * 0.09) * 0.16;
    var r = 1 - Math.abs(2 * vn(wx * 0.017 + 50, wy * 0.013 + 7) - 1), crack = Math.pow(r, 9) * 1.1;
    return slab * 0.2 + n * 1.3 - crack;
  }
  function bakeRock(inst, idx) {
    var th = inst.th, H = inst.H, W2 = VW >> 1, H2 = CH >> 1, top = (idx + 1) * CH, cv = mkCanvas(W2, H2), c = cv.getContext('2d');
    var img = c.createImageData(W2, H2), d = img.data, hh = new Float32Array((W2 + 2) * (H2 + 2)), x, y;
    var pal = {}, gl = th.glow.split(',').map(Number), lt = [gl[0] / 255, gl[1] / 255, gl[2] / 255], cool = [0.5, 0.58, 0.85];
    for (y = -1; y <= H2; y++) for (x = -1; x <= W2; x++) hh[(y + 1) * (W2 + 2) + x + 1] = rockH(x * 2, top - y * 2);
    function albedo(wy, wx) {
      var lvl = wy / LV, sI = lvlStratum(lvl), sh = lvlStratum((wy + strataEdge(sI, wx)) / LV);
      var key = sh + ':' + (H - lvl < 18 ? Math.round((H - lvl) / 2) : 99), a = pal[key];
      if (!a) { a = pal[key] = rgb(strataColor(th, sh < 0 ? 2 : 6 + 14 * sh + 1, H - lvl < 18 ? lvl : 0)); }
      return a;
    }
    for (y = 0; y < H2; y++) {
      var wy = top - y * 2, lvl = wy / LV, Il = 0.08 + 0.62 * Math.exp(-Math.max(0, lvl - 1) / 15), Ic = 0.5 + 0.25 * smooth(8, 60, lvl) ;
      for (x = 0; x < W2; x++) {
        var o = (y + 1) * (W2 + 2) + x + 1, wx = x * 2, hx = hh[o + 1] - hh[o - 1], hy = hh[o + W2 + 2] - hh[o - W2 - 2], h0 = hh[o];
        var nx = -hx * 3.4, ny = -hy * 3.4, il = 1 / Math.sqrt(nx * nx + ny * ny + 1);
        nx *= il; ny *= il; var nz = il;
        var d1 = Math.max(0, ny * 0.8 + nz * 0.6), d2 = Math.max(0, -ny * 0.7 - nx * 0.3 + nz * 0.64);
        var ao = 0.62 + 0.38 * clamp((h0 + 0.7) / 1.5, 0, 1), al = albedo(wy, wx), gr = 0.9 + 0.2 * vn(wx * 0.25, wy * 0.25);
        var ll = Il * (0.1 + 1.0 * d1 * d1 + 0.25 * d1), li = 0.12 + Ic * 0.55 * d2;
        var q = (y * W2 + x) * 4;
        d[q] = clamp(al[0] * gr * ao * (li * cool[0] + ll * lt[0] * 1.15) * 1.55, 0, 255);
        d[q + 1] = clamp(al[1] * gr * ao * (li * cool[1] + ll * lt[1] * 1.0) * 1.55, 0, 255);
        d[q + 2] = clamp(al[2] * gr * ao * (li * cool[2] + ll * lt[2] * 0.85) * 1.55, 0, 255);
        d[q + 3] = 255;
      }
    }
    c.putImageData(img, 0, 0);
    return cv;
  }

  // One baked piece of the main wall: strata (wavy layers), fine layer lines, boulders, cracks (some glow), bones,
  // hexagonal crystals and grain. Everything is a function of the chunk index and the theme: the same everywhere.
  function bakeWall(inst, idx) {
    var k = inst.k, th = inst.th, H = inst.H;
    var cv = mkCanvas(VW * k, CH * k), c = cv.getContext('2d');
    c.setTransform(k, 0, 0, k, 0, 0);
    var top = (idx + 1) * CH, bot = idx * CH, rnd = mulberry(idx * 7919 + 13), x, y, i;
    function cy(wy) { return top - wy; }
    // the rock itself: a height field (bedding planes, noise, cracks) lit like a normal map - warm lava from below, cool fill above
    c.imageSmoothingEnabled = true;
    c.drawImage(bakeRock(inst, idx), 0, 0, VW, CH);
    // fine layers
    for (i = 0; i < 16; i++) {
      var ly = rnd() * CH, ph = rnd() * 6, amp = 3 + rnd() * 7, dark = rnd() < 0.7;
      c.lineWidth = 1.5 + rnd() * 3; c.strokeStyle = dark ? 'rgba(0,0,0,.22)' : 'rgba(255,230,210,.07)';
      c.beginPath(); for (x = 0; x <= VW; x += 10) { var yv = ly + Math.sin(x * 0.02 + ph) * amp + Math.sin(x * 0.07 + ph * 2) * amp * 0.4; if (x) c.lineTo(x, yv); else c.moveTo(x, yv); } c.stroke();
    }
    // boulders: no outlines - lit from below by the lava, a cast shadow up the wall, a few chips
    for (i = 0; i < 12; i++) {
      var bx = rnd() * VW, by = rnd() * CH, br = 14 + rnd() * 34, lvl = (top - by) / LV, bc = strataColor(th, lvl, H), lk = 0.25 + 0.75 * Math.exp(-Math.max(0, lvl) / 18);
      c.save(); c.translate(3, -br * 0.22); blob(c, bx, by, br * 1.04, mulberry(i * 31 + idx)); c.fillStyle = 'rgba(0,0,0,.3)'; c.fill(); c.restore();
      var rr0 = mulberry(i * 31 + idx); blob(c, bx, by, br, rr0);
      var bg = c.createLinearGradient(bx, by - br, bx, by + br * 0.9);
      bg.addColorStop(0, shade(bc, 0.5)); bg.addColorStop(0.5, shade(bc, 0.95)); bg.addColorStop(1, mixc(shade(bc, 1.3), 'rgb(' + th.glow + ')', 0.42 * lk));
      c.fillStyle = bg; c.fill(); c.lineWidth = 1.2; c.strokeStyle = 'rgba(0,0,0,.35)'; c.stroke();
      c.save(); c.clip();
      var sg2 = c.createRadialGradient(bx - br * 0.4, by - br * 0.5, 2, bx - br * 0.4, by - br * 0.5, br * 0.9); sg2.addColorStop(0, 'rgba(170,190,230,.14)'); sg2.addColorStop(1, 'rgba(170,190,230,0)');
      c.fillStyle = sg2; c.fillRect(bx - br * 1.5, by - br * 1.5, br * 3, br * 3);
      c.strokeStyle = 'rgba(0,0,0,.4)'; c.lineWidth = 1.4; c.beginPath(); c.moveTo(bx - br * 0.3, by - br * 0.6); c.lineTo(bx - br * 0.05, by - br * 0.1); c.lineTo(bx + br * 0.25, by + br * 0.3); c.stroke();
      for (var sp3 = 0; sp3 < 10; sp3++) { c.fillStyle = 'rgba(0,0,0,.18)'; c.fillRect(bx + (rnd() - 0.5) * br * 1.6, by + (rnd() - 0.5) * br * 1.2, 1.5, 1.5); }
      c.restore();
      c.strokeStyle = 'rgba(' + th.glow + ',' + (0.4 * lk) + ')'; c.lineWidth = 2; c.beginPath(); c.ellipse(bx, by, br * 0.95, br * 0.82, 0, 0.45, Math.PI - 0.45); c.stroke();
    }
    // cracks (the low ones glow)
    for (i = 0; i < 8; i++) {
      var cx = rnd() * VW, cyy = rnd() * CH, lvl2 = (top - cyy) / LV, glow = lvl2 < 40 ? rnd() < 0.5 : rnd() < 0.12, pts = [[cx, cyy]];
      var ang = Math.PI / 2 + (rnd() - 0.5) * 0.9, n = 6 + Math.floor(rnd() * 8);
      for (var j = 0; j < n; j++) { ang += (rnd() - 0.5) * 1.0; cx += Math.cos(ang) * (9 + rnd() * 16); cyy += Math.sin(ang) * (9 + rnd() * 16); pts.push([cx, cyy]); }
      function tr() { c.beginPath(); for (var q = 0; q < pts.length; q++) { if (q) c.lineTo(pts[q][0], pts[q][1]); else c.moveTo(pts[q][0], pts[q][1]); } }
      c.lineJoin = 'round'; c.lineCap = 'round';
      if (glow) { c.strokeStyle = 'rgba(' + th.glow + ',.22)'; c.lineWidth = 9; tr(); c.stroke(); c.strokeStyle = 'rgba(' + th.glow + ',.55)'; c.lineWidth = 4; tr(); c.stroke();
        c.strokeStyle = th.lava2; c.lineWidth = 1.3; tr(); c.stroke(); }
      else { c.strokeStyle = 'rgba(0,0,0,.6)'; c.lineWidth = 2.4; tr(); c.stroke(); }
    }
    // embedded things: bones in the bone layers, crystals in the obsidian ones, a few anywhere
    var nb = 0;
    for (i = 0; i < 14; i++) {
      var ex = 20 + rnd() * (VW - 40), ey = 20 + rnd() * (CH - 40), el = (top - ey) / LV, es = lvlStratum(el), kind = rnd();
      if (es === 2 || es === 8 || es % 6 === 2) {
        if (kind < 0.35) skull(c, ex, ey, 9 + rnd() * 6, th, (rnd() - 0.5) * 0.8);
        else if (kind < 0.7) femur(c, ex, ey, 34 + rnd() * 28, th, (rnd() - 0.5) * 2.4);
        else if (nb < 3) { nb++; femur(c, ex, ey, 22 + rnd() * 16, th, rnd() * 3); }
      } else if (es % 6 === 3 || es % 6 === 4) {
        if (kind < 0.45) hexGem(c, ex, ey, 6 + rnd() * 9, rnd() < 0.5 ? rgba(th.accent2, 0.9) : rgba(th.accent, 0.85), rnd());
      } else if (kind < 0.12) skull(c, ex, ey, 8 + rnd() * 4, th, (rnd() - 0.5));
    }
    // grain
    for (i = 0; i < 260; i++) { c.fillStyle = rnd() < 0.5 ? 'rgba(0,0,0,.07)' : 'rgba(255,240,220,.05)'; var gs = 1 + rnd() * 3; c.fillRect(rnd() * VW, rnd() * CH, gs, gs); }
    // hexagonal windows through the wall: the far cave shows through, and slides by slower than the wall (parallax)
    for (var wi = 0; wi < 2; wi++) {
      if (hash2(idx, 11 + wi) < 0.1) continue;
      var wside = wi ? 1 : -1, wcx = wside < 0 ? 58 + hash2(idx, 21 + wi) * 74 : VW - 58 - hash2(idx, 31 + wi) * 74;
      var wcy = 56 + hash2(idx, 41 + wi) * (CH - 112), wr = 36 + hash2(idx, 51 + wi) * 22;
      hexPath(c, wcx, wcy, wr + 12);
      var rg = c.createLinearGradient(wcx - wr, wcy - wr, wcx + wr, wcy + wr);
      rg.addColorStop(0, shade(th.rockHi, 1.15)); rg.addColorStop(0.5, shade(th.rockHi, 0.7)); rg.addColorStop(1, shade(th.rockHi, 0.38));
      c.fillStyle = rg; c.fill(); c.lineWidth = 3; c.strokeStyle = 'rgba(10,2,4,.85)'; c.stroke();
      hexPath(c, wcx, wcy, wr); c.lineWidth = 8; c.strokeStyle = 'rgba(0,0,0,.55)'; c.stroke();
      c.save(); c.globalCompositeOperation = 'destination-out'; hexPath(c, wcx, wcy, wr); c.fillStyle = '#000'; c.fill(); c.restore();
    }
    // the sides fall into shadow
    var eg = c.createLinearGradient(0, 0, 90, 0); eg.addColorStop(0, 'rgba(0,0,0,.55)'); eg.addColorStop(1, 'rgba(0,0,0,0)');
    c.fillStyle = eg; c.fillRect(0, 0, 90, CH);
    var eg2 = c.createLinearGradient(VW - 90, 0, VW, 0); eg2.addColorStop(0, 'rgba(0,0,0,0)'); eg2.addColorStop(1, 'rgba(0,0,0,.55)');
    c.fillStyle = eg2; c.fillRect(VW - 90, 0, 90, CH);
    return cv;
  }

  // ---- little props used by the routes and the scene
  function slab(c, x, y, w, h, th, tone, rnd) {
    // a rock ledge: the top face catches the cold light, the front falls into shadow and its underside glows from the lava
    var x0 = x - w / 2, x1 = x + w / 2;
    c.save(); c.translate(0, 3); c.beginPath(); c.moveTo(x0 + 3, y - 3); c.lineTo(x1 - 4, y - 4); c.lineTo(x1 + 1, y + 2); c.lineTo(x1 - 6, y + h); c.lineTo(x0 + 9, y + h - 1); c.lineTo(x0 - 2, y + 4); c.closePath();
    c.fillStyle = 'rgba(0,0,0,.28)'; c.fill(); c.restore();
    c.beginPath();
    c.moveTo(x0 + 3, y - 3); c.lineTo(x1 - 4, y - 4); c.lineTo(x1 + 1, y + 2); c.lineTo(x1 - 6, y + h);
    c.lineTo(x0 + 9, y + h - 1); c.lineTo(x0 - 2, y + 4); c.closePath();
    var g = c.createLinearGradient(0, y - 4, 0, y + h);
    g.addColorStop(0, shade(tone, 1.55)); g.addColorStop(0.26, shade(tone, 1.2)); g.addColorStop(0.3, shade(tone, 0.62)); g.addColorStop(0.8, shade(tone, 0.4)); g.addColorStop(1, mixc(shade(tone, 0.7), 'rgb(' + th.glow + ')', 0.5));
    c.fillStyle = g; c.fill(); c.lineWidth = 1.4; c.strokeStyle = 'rgba(10,2,4,.7)'; c.stroke();
    c.strokeStyle = 'rgba(200,215,245,.3)'; c.lineWidth = 1.2; c.beginPath(); c.moveTo(x0 + 8, y - 1.5); c.lineTo(x1 - 10, y - 2.5); c.stroke();
    c.strokeStyle = 'rgba(0,0,0,.35)'; c.lineWidth = 1; c.beginPath(); c.moveTo(x0 + w * 0.3, y + 2); c.lineTo(x0 + w * 0.34, y + h * 0.7); c.moveTo(x0 + w * 0.7, y + 3); c.lineTo(x0 + w * 0.66, y + h * 0.55); c.stroke();
    c.strokeStyle = 'rgba(' + th.glow + ',.4)'; c.lineWidth = 1.3; c.beginPath(); c.moveTo(x0 + 10, y + h - 1.5); c.lineTo(x1 - 8, y + h - 2); c.stroke();
  }
  function knob(c, x, y, r, tone) {
    c.beginPath(); c.ellipse(x, y, r, r * 0.85, 0, 0, TAU);
    var g = c.createRadialGradient(x - r * 0.3, y - r * 0.3, 1, x, y, r * 1.1);
    g.addColorStop(0, shade(tone, 1.7)); g.addColorStop(1, shade(tone, 0.55));
    c.fillStyle = g; c.fill(); c.lineWidth = 2; c.strokeStyle = 'rgba(10,2,4,.8)'; c.stroke();
  }
  function link(c, x, y, ang, w, h, th, flat) {
    c.save(); c.translate(x, y); c.rotate(ang);
    c.lineWidth = 5.6; c.strokeStyle = 'rgba(6,2,4,.9)';
    c.beginPath(); rr(c, -w / 2, -h / 2, w, h, Math.min(w, h) / 2.2); c.stroke();
    var g = c.createLinearGradient(0, -h / 2, 0, h / 2); g.addColorStop(0, th.ironHi); g.addColorStop(0.4, th.iron); g.addColorStop(0.75, shade(th.iron, 0.5)); g.addColorStop(1, mixc(th.iron, 'rgb(' + th.glow + ')', 0.55));
    c.lineWidth = 3.4; c.strokeStyle = g; c.beginPath(); rr(c, -w / 2, -h / 2, w, h, Math.min(w, h) / 2.2); c.stroke();
    c.lineWidth = 1; c.strokeStyle = 'rgba(255,255,255,.28)'; c.beginPath(); c.moveTo(-w / 2 + 3, -h / 2 + 1.4); c.lineTo(w / 2 - 6, -h / 2 + 1.4); c.stroke();
    var hs = Math.round(Math.abs(x * 7 + y * 3)) % 7;                      // rust
    c.fillStyle = 'rgba(122,58,26,.55)'; c.fillRect(-w / 2 + hs, -h / 2 + 3, 2.2, 1.8); c.fillRect(w / 2 - 3 - hs * 0.4, h / 2 - 4, 1.8, 2.2);
    c.restore();
  }
  function rivet(c, x, y, r, th) {
    c.fillStyle = th.ironHi; c.strokeStyle = 'rgba(10,2,4,.8)'; c.lineWidth = 1.6;
    c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill(); c.stroke();
  }

  // ==================================================================== the routes (per frame)
  var DEFAULT_ROUTES = [{ from: 0, to: 7, kind: 'ledge' }];

  var DRAW = {};                    // drawing methods: DRAW.name(inst, c, sc, ...)

  function W2S(inst, wx, wy) { return { x: LANE_X + wx - inst.cam.x, y: ANCHOR_Y - (wy - inst.cam.y) }; }

  DRAW.routes = function (I, c, sc, T) {
    var tl = sc.tl, routes = tl ? tl.script.routes : DEFAULT_ROUTES, cam = I.cam;
    var lo = Math.floor((cam.y - (VH - ANCHOR_Y) - 140) / LV), hi = Math.ceil((cam.y + ANCHOR_Y + 180) / LV);
    for (var i = 0; i < routes.length; i++) {
      var s = routes[i];
      if (s.to < lo || s.from > hi) continue;
      var a = Math.max(s.from, lo), b = Math.min(s.to, hi);
      DRAW['route_' + s.kind](I, c, sc, s, a, b, T);
    }
  };

  DRAW.route_ledge = function (I, c, sc, s, a, b, T) {
    var th = I.th, tl = sc.tl, to = Math.min(b, s.to - 1);
    for (var L = a; L <= to; L++) {
      var tone = strataColor(th, L, I.H), rk = L * 7 + 3;
      var broken = tl && tl.loose[L] != null ? sc.tms - tl.loose[L] : -1;
      var lf = holdXY('ledge', 'fl', L, T), rt = holdXY('ledge', 'fr', L, T), hl = holdXY('ledge', 'hl', L, T), hr = holdXY('ledge', 'hr', L, T);
      var p1 = W2S(I, lf.x, lf.y), p2 = W2S(I, rt.x, rt.y), h1 = W2S(I, hl.x, hl.y), h2 = W2S(I, hr.x, hr.y);
      var tone2 = mixc(tone, th.rockHi, 0.45);
      knob(c, h1.x, h1.y, 6.5, tone2); knob(c, h2.x, h2.y, 6.5, tone2);
      if (broken < 0) {
        slab(c, p1.x, p1.y, 64 + hash(rk) * 18, 13, th, tone2);
        slab(c, p2.x, p2.y, 58 + hash(rk + 1) * 16, 12, th, tone2);
        if (tl && tl.crack[L] && sc.level >= L - 1.6) {                // a ledge that is about to give: hairline cracks (only once he is close)
          c.strokeStyle = 'rgba(10,2,4,.85)'; c.lineWidth = 1.8; c.beginPath();
          c.moveTo(p1.x - 6, p1.y - 2); c.lineTo(p1.x - 1, p1.y + 5); c.lineTo(p1.x - 5, p1.y + 10); c.moveTo(p1.x + 10, p1.y - 3); c.lineTo(p1.x + 6, p1.y + 4); c.stroke();
        }
      } else {
        var ft = clamp(broken / 900, 0, 1);
        if (ft < 1) {
          // the ledge crumbles: pieces fall and spin away
          for (var q = 0; q < 6; q++) {
            var r = hash2(L, q + 11), fx = p1.x - 26 + r * 56 + (r - 0.5) * ft * 30, fy = p1.y + 4 + ft * ft * 420 * (0.6 + hash2(L, q + 40)) , fa = ft * (r - 0.5) * 12;
            c.save(); c.translate(fx, fy); c.rotate(fa); c.globalAlpha = 1 - ft * 0.7;
            slab(c, 0, 0, 18 + r * 12, 8, th, tone2); c.restore();
          }
        }
        slab(c, p1.x - 18, p1.y, 22, 8, th, tone2);                // a stump is all that is left
        slab(c, p2.x, p2.y, 58, 12, th, tone2);
      }
    }
  };

  DRAW.route_chimney = function (I, c, sc, s, a, b, T) {
    var th = I.th, y0 = s.from * LV - 26, y1 = s.to * LV + 80;
    var sy1 = W2S(I, 0, y1).y, sy0 = W2S(I, 0, y0).y, x0 = LANE_X - 64, x1 = LANE_X + 64, i, y;
    if (sy1 > VH + 20 || sy0 < -20) return;
    var ya = Math.max(sy1, -10), yb = Math.min(sy0, VH + 10);
    c.save();
    c.beginPath();
    c.moveTo(x0 + wobble(ya, 1), ya);
    for (y = ya; y <= yb; y += 14) c.lineTo(x0 + wobble(y - I.cam.y * 1, 1), y);
    c.lineTo(x0 + wobble(yb, 1), yb);
    c.lineTo(x1 + wobble(yb, 2), yb);
    for (y = yb; y >= ya; y -= 14) c.lineTo(x1 + wobble(y - I.cam.y * 1, 2), y);
    c.closePath();
    var g = c.createLinearGradient(x0, 0, x1, 0);
    g.addColorStop(0, '#0b0409'); g.addColorStop(0.5, '#1b0b14'); g.addColorStop(1, '#0b0409');
    c.fillStyle = g; c.fill();
    c.lineWidth = 6; c.strokeStyle = shade(mixc(th.rockHi, th.strata[1], 0.6), 0.9); c.stroke();
    c.lineWidth = 2; c.strokeStyle = 'rgba(255,230,200,.18)'; c.stroke();
    c.restore();
    // a glow from way down the crack, rubble where he grips
    for (i = a; i <= Math.min(b, s.to - 1); i++) {
      var h1 = holdXY('chimney', 'hl', i, T), h2 = holdXY('chimney', 'hr', i, T), f1 = holdXY('chimney', 'fl', i, T), f2 = holdXY('chimney', 'fr', i, T);
      [h1, h2, f1, f2].forEach(function (h, q) { var p = W2S(I, h.x, h.y); knob(c, p.x + (h.x < 0 ? -3 : 3), p.y, 6.5, mixc(th.rockHi, '#2a1a1a', 0.4)); });
    }
  };
  function wobble(y, k) { return Math.sin(y * 0.031 + k * 2.3) * 5 + Math.sin(y * 0.083 + k) * 3; }

  DRAW.route_chains = function (I, c, sc, s, a, b, T) {
    var th = I.th, y0 = s.from * LV - 10, y1 = s.to * LV + 62;
    var syTop = W2S(I, 0, y1).y, syBot = W2S(I, 0, y0).y;
    if (syTop > VH + 20 || syBot < -20) return;
    // the bracket the chains hang from
    var top = W2S(I, 0, y1);
    if (top.y > -40 && top.y < VH + 40) {
      c.fillStyle = th.iron; c.strokeStyle = 'rgba(10,2,4,.85)'; c.lineWidth = 2.6;
      c.beginPath(); rr(c, LANE_X - 46, top.y - 14, 92, 26, 5); c.fill(); c.stroke();
      rivet(c, LANE_X - 36, top.y - 1, 3.4, th); rivet(c, LANE_X + 36, top.y - 1, 3.4, th); rivet(c, LANE_X - 10, top.y - 1, 3.4, th); rivet(c, LANE_X + 10, top.y - 1, 3.4, th);
    }
    var ya = Math.max(syTop + 12, -20), yb = Math.min(syBot, VH + 20);
    [-18, 18].forEach(function (cx) {
      var n = 0;
      for (var y = syTop + 12; y < syBot; y += 14, n++) {
        if (y < ya || y > yb) continue;
        var wy = I.cam.y + ANCHOR_Y - y;
        link(c, LANE_X + cx + sway('chains', wy, T), y, 0, n % 2 ? 8 : 14, n % 2 ? 17 : 14, th, n % 2);
      }
    });
    // rungs between the two chains where he puts his feet
    for (var L = a; L <= Math.min(b, s.to - 1); L++) {
      [0, 28].forEach(function (off) {
        var wy = L * LV + off, p = W2S(I, 0, wy), sw = sway('chains', wy, T);
        c.strokeStyle = 'rgba(10,2,4,.9)'; c.lineWidth = 7; c.lineCap = 'round'; c.beginPath(); c.moveTo(LANE_X - 18 + sw, p.y); c.lineTo(LANE_X + 18 + sw, p.y); c.stroke();
        c.strokeStyle = th.ironHi; c.lineWidth = 3.6; c.beginPath(); c.moveTo(LANE_X - 18 + sw, p.y); c.lineTo(LANE_X + 18 + sw, p.y); c.stroke();
      });
    }
  };

  DRAW.route_rope = function (I, c, sc, s, a, b, T) {
    var th = I.th, y0 = s.from * LV - 14, y1 = s.to * LV + 74, tl = sc.tl;
    var syTop = W2S(I, 0, y1).y, syBot = W2S(I, 0, y0).y;
    if (syTop > VH + 20 || syBot < -20) return;
    var top = W2S(I, 0, y1 + 8);
    if (top.y > -30 && top.y < VH + 30) {                         // an iron spike driven into the rock, with a ring
      c.fillStyle = th.iron; c.strokeStyle = 'rgba(10,2,4,.85)'; c.lineWidth = 2.4;
      c.beginPath(); c.moveTo(LANE_X - 24, top.y - 10); c.lineTo(LANE_X + 22, top.y - 14); c.lineTo(LANE_X + 8, top.y + 6); c.lineTo(LANE_X - 10, top.y + 4); c.closePath(); c.fill(); c.stroke();
      c.strokeStyle = th.ironHi; c.lineWidth = 3.4; c.beginPath(); c.arc(LANE_X, top.y + 6, 8, 0, TAU); c.stroke();
    }
    var snap = tl && tl.rope[s.from] != null ? sc.tms - tl.rope[s.from] : -1;       // a fray in this stretch (the rope is fine)
    var y = Math.max(syTop + 8, -10), yEnd = Math.min(syBot, VH + 10), pts = [], wy;
    for (; y <= yEnd; y += 8) { wy = I.cam.y + ANCHOR_Y - y; pts.push([LANE_X + sway('rope', wy, T), y]); }
    if (pts.length < 2) return;
    function path() { c.beginPath(); for (var i = 0; i < pts.length; i++) { if (i) c.lineTo(pts[i][0], pts[i][1]); else c.moveTo(pts[i][0], pts[i][1]); } }
    c.lineCap = 'round'; c.lineJoin = 'round';
    c.strokeStyle = 'rgba(10,2,4,.9)'; c.lineWidth = 16; path(); c.stroke();
    c.strokeStyle = th.rope; c.lineWidth = 11.5; path(); c.stroke();
    c.strokeStyle = th.rope2; c.lineWidth = 2.4;
    for (var i = 0; i < pts.length; i++) {                        // the twist
      var wy2 = I.cam.y + ANCHOR_Y - pts[i][1];
      if (Math.floor(wy2 / 8) % 2 === 0) { c.beginPath(); c.moveTo(pts[i][0] - 5, pts[i][1] + 4); c.lineTo(pts[i][0] + 5, pts[i][1] - 4); c.stroke(); }
    }
    for (var L = a; L <= Math.min(b, s.to - 1); L++) {            // knots where he grips
      [0, 28].forEach(function (off) {
        var wyy = L * LV + off, p = W2S(I, 0, wyy), sw = sway('rope', wyy, T);
        c.beginPath(); c.ellipse(LANE_X + sw, p.y, 9.5, 7, 0, 0, TAU); c.fillStyle = th.rope; c.fill(); c.lineWidth = 2.2; c.strokeStyle = 'rgba(10,2,4,.85)'; c.stroke();
        c.strokeStyle = th.rope2; c.lineWidth = 1.4; c.beginPath(); c.moveTo(LANE_X + sw - 6, p.y + 2); c.lineTo(LANE_X + sw + 6, p.y - 2); c.stroke();
      });
    }
  };

  DRAW.route_ribs = function (I, c, sc, s, a, b, T) {
    var th = I.th, y0 = s.from * LV - 14, y1 = s.to * LV + 60, L;
    var syTop = W2S(I, 0, y1).y, syBot = W2S(I, 0, y0).y;
    if (syTop > VH + 20 || syBot < -20) return;
    var outline = 'rgba(30,10,10,.85)';
    // the spine
    for (var y = Math.max(syTop, -20); y < Math.min(syBot, VH + 20); y += 21) {
      c.fillStyle = th.bone2; c.strokeStyle = outline; c.lineWidth = 2.2;
      c.beginPath(); rr(c, LANE_X - 13, y, 26, 15, 6); c.fill(); c.stroke();
      c.fillStyle = th.bone; c.beginPath(); rr(c, LANE_X - 9, y + 2, 14, 5, 2.5); c.fill();
      c.fillStyle = th.bone2; c.beginPath(); c.moveTo(LANE_X - 4, y); c.lineTo(LANE_X + 1, y - 7); c.lineTo(LANE_X + 6, y); c.fill();
    }
    for (L = a; L <= Math.min(b, s.to - 1); L++) {
      for (var sd = -1; sd <= 1; sd += 2) {
        var off = sd < 0 ? 0 : 28, wy = L * LV + off, p = W2S(I, 0, wy), tipx = LANE_X + sd * 44, tipy = p.y + 4;
        c.lineCap = 'round';
        c.strokeStyle = outline; c.lineWidth = 12;
        c.beginPath(); c.moveTo(LANE_X + sd * 8, p.y - 12); c.quadraticCurveTo(LANE_X + sd * 38, p.y - 26, tipx, tipy); c.stroke();
        c.strokeStyle = th.bone; c.lineWidth = 8;
        c.beginPath(); c.moveTo(LANE_X + sd * 8, p.y - 12); c.quadraticCurveTo(LANE_X + sd * 38, p.y - 26, tipx, tipy); c.stroke();
        c.strokeStyle = 'rgba(255,255,255,.35)'; c.lineWidth = 2;
        c.beginPath(); c.moveTo(LANE_X + sd * 10, p.y - 15); c.quadraticCurveTo(LANE_X + sd * 38, p.y - 29, tipx - sd * 2, tipy - 4); c.stroke();
        c.fillStyle = th.bone; c.strokeStyle = outline; c.lineWidth = 2.2;
        c.beginPath(); c.arc(tipx, tipy, 6.5, 0, TAU); c.fill(); c.stroke();
      }
    }
  };

  // ==================================================================== ruler, torches, plaques
  DRAW.ruler = function (I, c, sc, T) {
    var th = I.th, cam = I.cam, lo = Math.floor((cam.y - (VH - ANCHOR_Y) - 40) / LV), hi = Math.ceil((cam.y + ANCHOR_Y + 40) / LV);
    c.fillStyle = 'rgba(8,2,6,.62)'; c.fillRect(0, 0, 36, VH);
    c.strokeStyle = 'rgba(255,225,190,.22)'; c.lineWidth = 2; c.beginPath(); c.moveTo(36, 0); c.lineTo(36, VH); c.stroke();
    c.font = '800 13px ' + th.font; c.textAlign = 'left'; c.textBaseline = 'middle';
    for (var L = Math.max(0, lo); L <= Math.min(I.H, hi); L++) {
      var y = ANCHOR_Y - (L * LV - cam.y);
      c.strokeStyle = L % 5 === 0 ? 'rgba(255,225,190,.8)' : 'rgba(255,225,190,.38)'; c.lineWidth = L % 5 === 0 ? 2.4 : 1.6;
      c.beginPath(); c.moveTo(0, y); c.lineTo(L % 5 === 0 ? 24 : 12, y); c.stroke();
      if (L % 5 === 0 && L > 0) {
        c.fillStyle = 'rgba(255,240,220,.95)'; c.fillText(String(L), 8, y - 11);
      }
    }
  };

  DRAW.torches = function (I, c, sc, T) {
    var th = I.th, cam = I.cam, lo = Math.floor((cam.y - (VH - ANCHOR_Y) - 100) / LV), hi = Math.ceil((cam.y + ANCHOR_Y + 120) / LV);
    for (var L = Math.max(2, lo); L <= Math.min(I.H - 2, hi); L++) {
      var side = 0;
      if (L % 9 === 4) side = -1; else if (L % 11 === 7) side = 1; else continue;
      var p = W2S(I, side * (side < 0 ? 190 : 250), L * LV + 20), fl = Math.sin(T / 90 + L) * 0.5 + Math.sin(T / 37 + L * 2) * 0.3;
      // the glow on the wall
      var gr = c.createRadialGradient(p.x, p.y - 10, 4, p.x, p.y - 10, 120);
      gr.addColorStop(0, 'rgba(' + th.glow + ',' + (0.34 + fl * 0.06) + ')'); gr.addColorStop(1, 'rgba(' + th.glow + ',0)');
      c.fillStyle = gr; c.fillRect(p.x - 130, p.y - 140, 260, 260);
      // the iron sconce and the flame
      c.fillStyle = th.iron; c.strokeStyle = 'rgba(10,2,4,.85)'; c.lineWidth = 2.2;
      c.beginPath(); c.moveTo(p.x - 7, p.y + 12); c.lineTo(p.x + 7, p.y + 12); c.lineTo(p.x + 11, p.y - 6); c.lineTo(p.x - 11, p.y - 6); c.closePath(); c.fill(); c.stroke();
      c.beginPath(); c.moveTo(p.x, p.y + 12); c.lineTo(p.x, p.y + 26); c.stroke();
      var h = 24 + fl * 6;
      c.fillStyle = th.lava0; c.beginPath(); c.moveTo(p.x - 9, p.y - 6); c.quadraticCurveTo(p.x - 12 + fl * 3, p.y - h * 0.6, p.x + fl * 4, p.y - h); c.quadraticCurveTo(p.x + 12 - fl * 3, p.y - h * 0.5, p.x + 9, p.y - 6); c.closePath(); c.fill();
      c.fillStyle = th.lava1; c.beginPath(); c.moveTo(p.x - 5, p.y - 6); c.quadraticCurveTo(p.x - 6, p.y - h * 0.45, p.x + fl * 2, p.y - h * 0.72); c.quadraticCurveTo(p.x + 7, p.y - h * 0.4, p.x + 5, p.y - 6); c.closePath(); c.fill();
      c.fillStyle = th.lava2; c.beginPath(); c.ellipse(p.x, p.y - 8, 2.6, 5, 0, 0, TAU); c.fill();
    }
  };

  // ==================================================================== bottom: the start platform + the lava
  DRAW.platform = function (I, c, sc, T) {
    var th = I.th, cam = I.cam, top = ANCHOR_Y - (0 - cam.y), x0 = LANE_X + PLAT.x0, x1 = LANE_X + PLAT.x1;
    if (top > VH + 120 || top < -400) return;
    var rnd = mulberry(77), i;
    c.beginPath();
    c.moveTo(x0 - 40, top - 3); c.lineTo(x1 + 4, top - 2); c.lineTo(x1 + 8, top + 22);
    var n = 9;
    for (i = 0; i <= n; i++) { var xx = x1 - (x1 - x0 + 40) * i / n; c.lineTo(xx + (i % 2 ? 6 : -6), top + 40 + (i % 3) * 22 + rnd() * 24); }
    c.closePath();
    var g = c.createLinearGradient(0, top, 0, top + 110);
    g.addColorStop(0, shade(th.strata[1], 1.5)); g.addColorStop(0.18, shade(th.strata[1], 1.1)); g.addColorStop(1, shade(th.strata[0], 0.55));
    c.fillStyle = g; c.fill(); c.lineWidth = 3; c.strokeStyle = 'rgba(10,2,4,.9)'; c.stroke();
    c.strokeStyle = 'rgba(255,235,210,.4)'; c.lineWidth = 2; c.beginPath(); c.moveTo(x0 - 30, top - 2); c.lineTo(x1 - 2, top - 1); c.stroke();
    // pebbles and bones on it
    skull(c, x0 + 70, top - 8, 8, th, 0.3); femur(c, x0 + 108, top - 3, 30, th, 0.15);
    // the sign: EXIT, so far up
    var sx = x0 + 150;
    c.fillStyle = '#6b4423'; c.strokeStyle = 'rgba(10,2,4,.9)'; c.lineWidth = 2.6;
    c.beginPath(); rr(c, sx - 3, top - 70, 7, 70, 2); c.fill(); c.stroke();
    c.beginPath(); rr(c, sx - 38, top - 96, 84, 34, 5); c.fill(); c.stroke();
    c.fillStyle = '#ffe9b8'; c.font = '900 17px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText('EXIT ↑', sx + 4, top - 84); c.font = '800 11px ' + th.font; c.fillText(I.H + ' levels', sx + 4, top - 70);
    c.textBaseline = 'alphabetic';
  };

  DRAW.lava = function (I, c, sc, T) {
    var th = I.th, cam = I.cam, ys = ANCHOR_Y - (LAVA_Y - cam.y);
    if (ys > VH + 30) return;
    var top = Math.max(ys, -20), x;
    var g = c.createLinearGradient(0, ys, 0, ys + 260);
    g.addColorStop(0, th.lava1); g.addColorStop(0.12, th.lava0); g.addColorStop(0.5, shade(th.lava0, 0.55)); g.addColorStop(1, th.lavaDeep);
    c.fillStyle = g; c.beginPath(); c.moveTo(0, VH + 20); c.lineTo(0, ys + wave(0, T));
    for (x = 0; x <= VW; x += 10) c.lineTo(x, ys + wave(x, T));
    c.lineTo(VW, VH + 20); c.closePath(); c.fill();
    // bright crest
    c.strokeStyle = th.lava2; c.lineWidth = 3; c.globalAlpha = 0.85; c.beginPath();
    for (x = 0; x <= VW; x += 10) { var yy = ys + wave(x, T) + 1; if (x) c.lineTo(x, yy); else c.moveTo(x, yy); } c.stroke(); c.globalAlpha = 1;
    // the bloom: lava light spilling up the pit (additive)
    c.save(); c.globalCompositeOperation = 'lighter';
    var bg2 = c.createLinearGradient(0, ys - 300, 0, ys + 10); bg2.addColorStop(0, 'rgba(' + th.glow + ',0)'); bg2.addColorStop(1, 'rgba(' + th.glow + ',.34)');
    c.fillStyle = bg2; c.fillRect(0, Math.max(0, ys - 300), VW, Math.min(VH, 310));
    var bg3 = c.createRadialGradient(VW * 0.45, ys, 10, VW * 0.45, ys, 360); bg3.addColorStop(0, 'rgba(255,200,120,.24)'); bg3.addColorStop(1, 'rgba(255,120,40,0)');
    c.fillStyle = bg3; c.fillRect(0, Math.max(0, ys - 360), VW, 400);
    c.restore();
    // darker crust floating on it
    for (var i = 0; i < 9; i++) {
      var r = hash(i * 31), bx = ((r * 900 + T * (6 + i % 3 * 2) / 1000 * (i % 2 ? 1 : -1)) % (VW + 160) + VW + 160) % (VW + 160) - 80, by = ys + 22 + (i % 4) * 34 + hash(i) * 20;
      if (by > VH + 10) continue;
      c.fillStyle = 'rgba(40,6,2,.65)'; c.beginPath(); c.ellipse(bx, by, 34 + r * 20, 7 + r * 4, 0, 0, TAU); c.fill();
    }
    // bubbles
    for (var j = 0; j < 14; j++) {
      var ph = ((T / (900 + j * 70)) + hash(j * 17)) % 1, bx2 = hash(j * 13 + 5) * VW, by2 = ys + 20 + hash(j * 7) * 200 - ph * 14, rad = ph < 0.8 ? 2 + ph * 7 : 0;
      if (rad && by2 < VH) { c.strokeStyle = 'rgba(255,240,170,' + (0.8 - ph * 0.6) + ')'; c.lineWidth = 1.6; c.fillStyle = 'rgba(255,170,40,.35)'; c.beginPath(); c.arc(bx2, by2, rad, 0, TAU); c.fill(); c.stroke(); }
    }
  };
  function wave(x, T) { return Math.sin(x / 33 + T / 520) * 4 + Math.sin(x / 13 - T / 330) * 2.2; }

  // the very top of the pit: daylight, a lip of grass and a sign. (Only seen by a soul who gets there.)
  DRAW.top = function (I, c, sc, T) {
    var th = I.th, cam = I.cam, rimY = I.H * LV + 14, ys = ANCHOR_Y - (rimY - cam.y);
    if (ys < -130) return;                                // the rim is above the screen: all wall
    if (ys > VH + 130) { I._sky(c, 0, VH, ys, T); return; }   // below the screen: all sky
    I._sky(c, 0, Math.min(VH, ys), ys, T);
    // the grass lip
    var x;
    c.beginPath(); c.moveTo(0, ys + 70); c.lineTo(0, ys - 6);
    for (x = 0; x <= VW; x += 14) c.lineTo(x, ys - 4 - hash(x) * 8 - (x % 28 ? 0 : 5));
    c.lineTo(VW, ys + 70); c.closePath();
    var g = c.createLinearGradient(0, ys - 10, 0, ys + 70); g.addColorStop(0, '#6bd46a'); g.addColorStop(0.16, '#3c9e4a'); g.addColorStop(0.2, '#5a3a22'); g.addColorStop(1, '#2a1810');
    c.fillStyle = g; c.fill(); c.lineWidth = 3; c.strokeStyle = 'rgba(10,30,10,.7)'; c.stroke();
    for (x = 20; x < VW; x += 46) {                       // a few flowers
      var fy = ys - 8 - hash(x * 3) * 8; c.fillStyle = hash(x) < 0.5 ? '#ffd23f' : '#ff6fa8'; c.beginPath(); c.arc(x, fy, 4, 0, TAU); c.fill();
      c.strokeStyle = '#2f7a38'; c.lineWidth = 2; c.beginPath(); c.moveTo(x, fy); c.lineTo(x, fy + 8); c.stroke();
    }
    // the sign
    var sx = LANE_X - 112;
    c.fillStyle = '#7a4d28'; c.strokeStyle = 'rgba(10,2,4,.9)'; c.lineWidth = 2.6;
    c.beginPath(); rr(c, sx - 4, ys - 66, 8, 66, 2); c.fill(); c.stroke();
    c.beginPath(); rr(c, sx - 62, ys - 100, 124, 40, 6); c.fill(); c.stroke();
    c.fillStyle = '#fff3d0'; c.font = '900 18px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText('WELCOME TO', sx, ys - 88); c.fillText('EARTH', sx, ys - 69); c.textBaseline = 'alphabetic';
  };
  CL.prototype._sky = function (c, y0, y1, horizon, T) {
    var th = this.th;
    var g = c.createLinearGradient(0, y0, 0, Math.max(y1, y0 + 1));
    g.addColorStop(0, '#6aa8ff'); g.addColorStop(1, mixc(th.skyTop, '#ffd0a0', 0.5));
    c.fillStyle = g; c.fillRect(0, y0, VW, Math.max(0, y1 - y0));
    // a sun and clouds that drift
    var sy = Math.min(y1 - 60, y0 + 160);
    var sg = c.createRadialGradient(VW - 130, sy, 6, VW - 130, sy, 150); sg.addColorStop(0, 'rgba(255,250,210,.95)'); sg.addColorStop(0.2, 'rgba(255,230,160,.6)'); sg.addColorStop(1, 'rgba(255,220,150,0)');
    c.fillStyle = sg; c.fillRect(VW - 290, sy - 160, 320, 320);
    c.fillStyle = '#fff6d8'; c.beginPath(); c.arc(VW - 130, sy, 28, 0, TAU); c.fill();
    for (var i = 0; i < 4; i++) {
      var cx = ((hash(i * 9) * 800 + T / 40 * (1 + i * 0.3)) % 760) - 80, cy = y0 + 70 + i * 74 + hash(i) * 20;
      if (cy > y1 - 30) continue;
      c.fillStyle = 'rgba(255,255,255,.85)';
      for (var q = 0; q < 4; q++) { c.beginPath(); c.ellipse(cx + q * 22, cy - (q % 2) * 8, 28 + (q % 3) * 6, 15, 0, 0, TAU); c.fill(); }
    }
  };

  // ==================================================================== effects (stateless: a function of time)
  function burst(c, x, y, age, o) {
    // o: {n, seed, speed, g, life, size, colors[], spread (rad), dir (rad), shape}
    if (age < 0 || age > o.life) return;
    var t = age / o.life, sec = age / 1000, n = o.n || 12;
    for (var i = 0; i < n; i++) {
      var r1 = hash2(o.seed | 0, i * 3 + 1), r2 = hash2(o.seed | 0, i * 3 + 2), r3 = hash2(o.seed | 0, i * 3 + 3);
      var a = (o.dir == null ? -Math.PI / 2 : o.dir) + (r1 - 0.5) * (o.spread == null ? 2.4 : o.spread), v = (o.speed || 200) * (0.35 + r2 * 0.8);
      var px = x + Math.cos(a) * v * sec, py = y + Math.sin(a) * v * sec + 0.5 * (o.g == null ? 500 : o.g) * sec * sec;
      c.globalAlpha = clamp(1 - t * t, 0, 1);
      c.fillStyle = o.colors[Math.floor(r3 * o.colors.length) % o.colors.length];
      var sz = (o.size || 4) * (0.5 + r3) * (o.shrink ? 1 - t * 0.7 : 1);
      if (o.shape === 'star') { star(c, px, py, sz, 5, age / 90 + i); }
      else { c.beginPath(); c.arc(px, py, sz, 0, TAU); c.fill(); }
    }
    c.globalAlpha = 1;
  }
  function star(c, x, y, r, n, rot) {
    c.beginPath();
    for (var i = 0; i < n * 2; i++) { var a = rot + i * Math.PI / n, rad = i % 2 ? r * 0.45 : r; c.lineTo(x + Math.cos(a) * rad, y + Math.sin(a) * rad); }
    c.closePath(); c.fill();
  }
  function ring(c, x, y, age, life, r0, r1, color, w) {
    if (age < 0 || age > life) return;
    var t = age / life; c.globalAlpha = 1 - t; c.strokeStyle = color; c.lineWidth = w * (1 - t) + 1;
    c.beginPath(); c.arc(x, y, lerp(r0, r1, easeOut(t)), 0, TAU); c.stroke(); c.globalAlpha = 1;
  }
  function textPop(c, th, txt, x, y, age, life, color, size, rot) {
    if (age < 0 || age > life) return;
    var t = age / life, sc = t < 0.15 ? 0.4 + t / 0.15 * 0.9 : 1.3 - Math.min(0.3, (t - 0.15) * 0.6);
    c.save(); c.globalAlpha = 1 - smooth(0.65, 1, t); c.translate(x, y - t * 26); c.rotate(rot || 0); c.scale(sc, sc);
    c.font = '900 ' + (size || 30) + 'px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.lineJoin = 'round'; c.lineWidth = 7; c.strokeStyle = 'rgba(20,4,8,.92)'; c.strokeText(txt, 0, 0);
    c.fillStyle = color; c.fillText(txt, 0, 0); c.restore();
  }

  // ==================================================================== flags
  DRAW.flags = function (I, c, sc, T) {
    var th = I.th, ms = sc.markers || [], tl = sc.tl, i, prev = -99, col = 0, cols = [];
    for (i = 0; i < ms.length; i++) { col = ms[i].height - prev <= 2 ? (col + 1) % 3 : 0; prev = ms[i].height; cols[i] = col; }
    for (i = 0; i < ms.length; i++) {
      var m = ms[i], x = 74 + cols[i] * 94, p = W2S(I, x, m.height * LV);
      if (p.y < -150 || p.y > VH + 60) continue;
      var st = tl ? tl.flag(m.height, sc.tms) : 'open';
      var age = 0, lostAge = 0;
      if (tl && st === 'passed') age = sc.tms - tl.arrive[m.height];
      if (tl && st === 'lost') lostAge = sc.tms - tl.fallAt;
      I._flag(c, m, p.x, p.y, st, age, lostAge, T, i);
    }
  };
  CL.prototype._flag = function (c, m, x, y, st, age, lostAge, T, ci) {
    var th = this.th, top = m.height >= this.H;
    var col = st === 'passed' ? '#4fd66a' : st === 'lost' ? '#85808c' : (top ? '#ffd84a' : th.flag);
    var colD = st === 'passed' ? '#1f8a3a' : st === 'lost' ? '#4a4650' : shade(col, 0.55);
    // the ledge
    slab(c, x + 30, y, 112, 15, th, mixc(th.rockHi, th.strata[1], 0.35));
    // the pole
    var ph = 82;
    c.strokeStyle = 'rgba(10,2,4,.9)'; c.lineWidth = 6; c.lineCap = 'round'; c.beginPath(); c.moveTo(x, y - 2); c.lineTo(x, y - ph); c.stroke();
    c.strokeStyle = '#d8c9a8'; c.lineWidth = 3; c.beginPath(); c.moveTo(x, y - 2); c.lineTo(x, y - ph); c.stroke();
    c.fillStyle = '#ffe9a0'; c.strokeStyle = 'rgba(10,2,4,.9)'; c.lineWidth = 2; c.beginPath(); c.arc(x, y - ph - 2, 4.2, 0, TAU); c.fill(); c.stroke();
    // the pennant: waves in the heat; droops when lost; pops when passed
    var pop = st === 'passed' && age >= 0 && age < 700 ? Math.sin(age / 700 * Math.PI) : 0;
    var droop = st === 'lost' ? smooth(0, 520, lostAge) : 0;
    var w = 80 + pop * 14, h = 36 + pop * 6, fy = y - ph + 4, wv = Math.sin(T / 260 + ci) * 3 * (1 - droop);
    c.save(); c.translate(x, fy); c.rotate(droop * 1.25);
    c.beginPath(); c.moveTo(0, 0); c.lineTo(w * 0.5, wv * 0.5 - 1); c.lineTo(w, wv - 3); c.lineTo(w - 16, h * 0.5 + wv * 0.7); c.lineTo(w, h + wv + 3); c.lineTo(w * 0.5, h + wv * 0.5 + 1); c.lineTo(0, h); c.closePath();
    var gg = c.createLinearGradient(0, 0, 0, h); gg.addColorStop(0, col); gg.addColorStop(1, colD);
    c.fillStyle = gg; c.fill(); c.lineWidth = 2.6; c.strokeStyle = 'rgba(10,2,4,.9)'; c.stroke();
    c.fillStyle = st === 'lost' ? '#d6d2da' : '#2a0e06'; c.font = '900 ' + (top ? 15 : 19) + 'px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText(mult(m.mult), w * 0.46, h * 0.52 + wv * 0.5);
    if (st === 'passed') { c.fillStyle = '#fff'; c.font = '900 13px ' + th.font; c.fillText('✓', 11, h * 0.5); }
    c.restore();
    // the level + who bet on it
    var names = m.users.length === 1 ? m.users[0].user : m.users[0].user + ' +' + (m.users.length - 1);
    c.font = '800 12.5px ' + th.font; c.textBaseline = 'middle';
    var tw = Math.min(150, c.measureText(names).width + 14), lab = (top ? 'ESCAPE ' : 'LV ') + m.height;
    c.fillStyle = 'rgba(14,4,8,.82)'; c.strokeStyle = 'rgba(255,225,190,.35)'; c.lineWidth = 1.4;
    c.beginPath(); rr(c, x - 6, y + 14, tw, 19, 6); c.fill(); c.stroke();
    c.fillStyle = st === 'lost' ? '#b9b3c0' : '#fff3e0'; c.textAlign = 'left';
    var nm = names; while (nm.length > 3 && c.measureText(nm).width > tw - 12) nm = nm.slice(0, -2);
    c.fillText(nm === names ? nm : nm + '…', x + 1, y + 24);
    c.fillStyle = 'rgba(14,4,8,.82)'; c.beginPath(); rr(c, x + 38 + (tw - 44 > 0 ? tw - 44 : 0) - 0, y - 4, 0, 0, 0); c.fill();
    c.font = '900 11.5px ' + th.font; c.fillStyle = st === 'passed' ? '#a8ffb8' : '#ffd9a0'; c.textAlign = 'right';
    c.fillText(lab, x + 108, y + 8); c.textAlign = 'left'; c.textBaseline = 'alphabetic';
    // the pop: sparkles and the payout
    if (st === 'passed' && age >= 0 && age < 1100) {
      burst(c, x + 30, fy + 10, age, { n: 12, seed: m.height * 7 + 3, speed: 150, g: 260, life: 1000, size: 3.2, colors: ['#fff6a0', '#7dff9a', '#ffd23f'], shape: 'star', spread: 6.28, dir: 0 });
      textPop(c, th, mult(m.mult) + '!', x + 40, fy - 16, age - 60, 1000, '#7dff9a', 24);
    }
  };

  // ==================================================================== captions
  function wrap(c, text, maxW) {
    var words = String(text).split(' '), lines = [], cur = '';
    for (var i = 0; i < words.length; i++) {
      var t = cur ? cur + ' ' + words[i] : words[i];
      if (c.measureText(t).width > maxW && cur) { lines.push(cur); cur = words[i]; } else cur = t;
    }
    if (cur) lines.push(cur);
    return lines;
  }
  function bubble(c, th, text, x, y, age, life, who) {
    // a speech bubble whose tail points at (x, y); age / life in ms
    if (age < 0 || age > life) return;
    var pop = easeOut(age / 140), fade = 1 - smooth(life - 220, life, age);
    c.save(); c.globalAlpha = fade; c.translate(x, y); c.scale(0.7 + 0.3 * pop, 0.7 + 0.3 * pop);
    var fs = 15; c.font = '800 ' + fs + 'px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle';
    var lines = wrap(c, text, 190), w = 0, i;
    for (i = 0; i < lines.length; i++) w = Math.max(w, c.measureText(lines[i]).width);
    var bw = w + 24, bh = lines.length * (fs + 3) + 16, bx = -bw / 2, by = -bh - 20;
    var shiftX = clamp(x - 0, 42 + bw / 2, VW - 8 - bw / 2) - x;                // keep it on the screen (and off the ruler)
    c.translate(shiftX, 0);
    c.fillStyle = who === 'demon' ? '#ffe3e0' : '#fffaf0'; c.strokeStyle = '#2a1230'; c.lineWidth = 3; c.lineJoin = 'round';
    c.beginPath(); rr(c, bx, by, bw, bh, 12); c.fill(); c.stroke();
    c.beginPath(); c.moveTo(-9 - shiftX * 0.5, by + bh - 1); c.lineTo(-shiftX, -2); c.lineTo(7 - shiftX * 0.5, by + bh - 1); c.closePath(); c.fillStyle = who === 'demon' ? '#ffe3e0' : '#fffaf0'; c.fill();
    c.beginPath(); c.moveTo(-9 - shiftX * 0.5, by + bh); c.lineTo(-shiftX, -2); c.lineTo(7 - shiftX * 0.5, by + bh); c.stroke();
    c.fillStyle = '#2a1230';
    for (i = 0; i < lines.length; i++) c.fillText(lines[i], 0, by + 8 + (fs + 3) * i + (fs + 3) / 2);
    c.restore();
  }

  // ==================================================================== creatures
  function drawDemon(c, x, y, kind, o, T) {
    // sits on a little ledge at (x, y). o: {talk 0..1, point -1..1, stand 0..1, scale}
    var pal = [{ b: '#d6342c', bl: '#f0684a', h: '#2b1a12', w: '#8a1c1c' }, { b: '#7b45d4', bl: '#a679ee', h: '#241437', w: '#4a2290' }, { b: '#1f9c93', bl: '#52d0c4', h: '#10302c', w: '#106058' }][kind % 3];
    var s = o.scale || 1, bob = Math.sin(T / 240) * 1.5 + (o.talk ? Math.sin(T / 55) * 2.5 * o.talk : 0);
    c.save(); c.translate(x, y); c.scale(s, s);
    // the perch
    slab(c, 0, 0, 78, 13, I_TH, '#6a5048');
    c.translate(0, -2 - o.stand * 8 + bob);
    c.lineJoin = 'round'; c.lineCap = 'round';
    // wings
    c.fillStyle = pal.w; c.strokeStyle = '#1b0a12'; c.lineWidth = 2.6;
    [-1, 1].forEach(function (sd) {
      c.beginPath(); c.moveTo(sd * 8, -44); c.quadraticCurveTo(sd * 46, -76 + Math.sin(T / 120) * 3, sd * 40, -30); c.quadraticCurveTo(sd * 28, -40, sd * 20, -20); c.closePath(); c.fill(); c.stroke();
    });
    // tail
    c.strokeStyle = '#1b0a12'; c.lineWidth = 7; c.beginPath(); c.moveTo(-14, -10); c.bezierCurveTo(-44, -6, -52, -34 + Math.sin(T / 300) * 4, -34, -44); c.stroke();
    c.strokeStyle = pal.b; c.lineWidth = 3.4; c.beginPath(); c.moveTo(-14, -10); c.bezierCurveTo(-44, -6, -52, -34 + Math.sin(T / 300) * 4, -34, -44); c.stroke();
    c.fillStyle = pal.b; c.strokeStyle = '#1b0a12'; c.lineWidth = 2.4; c.beginPath(); c.moveTo(-34, -44); c.lineTo(-44, -52); c.lineTo(-27, -54); c.closePath(); c.fill(); c.stroke();
    // legs dangling
    [-1, 1].forEach(function (sd) {
      c.strokeStyle = '#1b0a12'; c.lineWidth = 12; c.beginPath(); c.moveTo(sd * 9, -10); c.lineTo(sd * 12 + Math.sin(T / 400 + sd) * 3, 14); c.stroke();
      c.strokeStyle = pal.b; c.lineWidth = 7.5; c.beginPath(); c.moveTo(sd * 9, -10); c.lineTo(sd * 12 + Math.sin(T / 400 + sd) * 3, 14); c.stroke();
      c.fillStyle = pal.h; c.beginPath(); c.ellipse(sd * 12 + Math.sin(T / 400 + sd) * 3, 16, 7, 5, 0, 0, TAU); c.fill();
    });
    // body
    var bg = c.createRadialGradient(-6, -30, 4, 0, -22, 34); bg.addColorStop(0, pal.bl); bg.addColorStop(1, pal.b);
    c.fillStyle = bg; c.strokeStyle = '#1b0a12'; c.lineWidth = 3.4;
    c.beginPath(); c.ellipse(0, -24, 25, 29, 0, 0, TAU); c.fill(); c.stroke();
    // a hexagon on the belly (the hex-demons' badge)
    c.beginPath(); for (var i = 0; i < 6; i++) { var a = i / 6 * TAU + TAU / 12; c.lineTo(Math.cos(a) * 9, -18 + Math.sin(a) * 9); } c.closePath();
    c.fillStyle = 'rgba(255,230,160,.9)'; c.fill(); c.strokeStyle = '#1b0a12'; c.lineWidth = 2; c.stroke();
    // head
    c.save(); c.translate(0, -56); c.rotate(Math.sin(T / 380) * 0.05 + (o.point || 0) * 0.12);
    [-1, 1].forEach(function (sd) {
      c.fillStyle = pal.h; c.beginPath(); c.moveTo(sd * 10, -12); c.quadraticCurveTo(sd * 24, -26, sd * 13, -40); c.quadraticCurveTo(sd * 14, -26, sd * 4, -16); c.closePath(); c.fill();
      c.fillStyle = pal.b; c.strokeStyle = '#1b0a12'; c.lineWidth = 2.6;
      c.beginPath(); c.moveTo(sd * 18, -2); c.lineTo(sd * 34, -9); c.lineTo(sd * 20, 8); c.closePath(); c.fill(); c.stroke();
    });
    c.fillStyle = pal.b; c.strokeStyle = '#1b0a12'; c.lineWidth = 3.2; c.beginPath(); c.ellipse(0, 0, 22, 20, 0, 0, TAU); c.fill(); c.stroke();
    // eyes + brow
    [-1, 1].forEach(function (sd) {
      c.fillStyle = '#ffe66a'; c.strokeStyle = '#1b0a12'; c.lineWidth = 2; c.beginPath(); c.ellipse(sd * 9, -4, 6, 5.4, 0, 0, TAU); c.fill(); c.stroke();
      c.fillStyle = '#1b0a12'; c.beginPath(); c.ellipse(sd * 9 + (o.point || 0) * 1.4, -4, 1.7, 4.4, 0, 0, TAU); c.fill();
      c.strokeStyle = '#1b0a12'; c.lineWidth = 3; c.beginPath(); c.moveTo(sd * 15, -13); c.lineTo(sd * 3, -9); c.stroke();
    });
    // grin
    var open = 0.4 + (o.talk || 0) * 0.6 * (0.5 + 0.5 * Math.sin(T / 70));
    c.fillStyle = '#4a0a14'; c.strokeStyle = '#1b0a12'; c.lineWidth = 2.4;
    c.beginPath(); c.moveTo(-13, 5); c.quadraticCurveTo(0, 8 + 14 * open, 13, 5); c.quadraticCurveTo(0, 6, -13, 5); c.closePath(); c.fill(); c.stroke();
    c.fillStyle = '#fff'; [-8, 0, 8].forEach(function (fx) { c.beginPath(); c.moveTo(fx - 2.4, 5.5); c.lineTo(fx + 2.4, 5.5); c.lineTo(fx, 10.5); c.closePath(); c.fill(); });
    c.restore();
    // arms and what he holds
    var arm = (o.point || 0);
    c.strokeStyle = '#1b0a12'; c.lineWidth = 11; c.beginPath(); c.moveTo(-20, -36); c.lineTo(-30, -20); c.stroke();
    c.strokeStyle = pal.b; c.lineWidth = 7; c.beginPath(); c.moveTo(-20, -36); c.lineTo(-30, -20); c.stroke();
    c.strokeStyle = '#1b0a12'; c.lineWidth = 11; c.beginPath(); c.moveTo(20, -36); c.lineTo(arm ? 22 + arm * 14 : 30, arm ? -62 : -22); c.stroke();
    c.strokeStyle = pal.b; c.lineWidth = 7; c.beginPath(); c.moveTo(20, -36); c.lineTo(arm ? 22 + arm * 14 : 30, arm ? -62 : -22); c.stroke();
    if (kind % 3 === 0) {                                  // a bucket of popcorn
      c.save(); c.translate(-34, -20); c.rotate(-0.15);
      c.fillStyle = '#fff'; c.strokeStyle = '#1b0a12'; c.lineWidth = 2.4; c.beginPath(); c.moveTo(-11, -12); c.lineTo(11, -12); c.lineTo(8, 14); c.lineTo(-8, 14); c.closePath(); c.fill(); c.stroke();
      c.fillStyle = '#e23a3a'; for (var q = -1; q <= 1; q++) { c.beginPath(); c.moveTo(q * 7 - 2, -12); c.lineTo(q * 7 + 2, -12); c.lineTo(q * 5 + 2, 14); c.lineTo(q * 5 - 2, 14); c.closePath(); c.fill(); }
      c.fillStyle = '#fff3c0'; for (q = 0; q < 6; q++) { c.beginPath(); c.arc(-8 + q * 3.4, -14 - (q % 2) * 4, 4.2, 0, TAU); c.fill(); c.stroke(); }
      c.restore();
    } else if (kind % 3 === 1) {                           // a scorecard
      c.save(); c.translate(-34, -22 - (o.talk ? 10 : 0)); c.rotate(-0.1);
      c.fillStyle = '#fff'; c.strokeStyle = '#1b0a12'; c.lineWidth = 2.4; c.beginPath(); rr(c, -14, -18, 28, 32, 4); c.fill(); c.stroke();
      c.fillStyle = '#c0182c'; c.font = '900 16px ' + I_TH.font; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(o.score || '3', 0, -4); c.font = '800 9px ' + I_TH.font; c.fillText('/ 10', 0, 8);
      c.restore();
    } else {                                               // a megaphone
      c.save(); c.translate(-30, -26); c.rotate(-0.5);
      c.fillStyle = '#e8e8f0'; c.strokeStyle = '#1b0a12'; c.lineWidth = 2.4; c.beginPath(); c.moveTo(-6, -5); c.lineTo(14, -12); c.lineTo(14, 12); c.lineTo(-6, 5); c.closePath(); c.fill(); c.stroke();
      c.fillStyle = '#c0182c'; c.beginPath(); c.rect(-12, -6, 7, 12); c.fill(); c.stroke();
      c.restore();
    }
    c.restore();
  }
  var I_TH = THEMES.inferno;            // (refreshed per frame: the little props use the current theme's font / tones)

  function drawBat(c, x, y, flap, s, mood) {
    c.save(); c.translate(x, y); c.scale(s, s);
    var w = Math.sin(flap) * 0.9;
    c.fillStyle = '#5a3a5c'; c.strokeStyle = '#140612'; c.lineWidth = 2.6; c.lineJoin = 'round';
    [-1, 1].forEach(function (sd) {
      c.beginPath(); c.moveTo(sd * 4, -2); c.quadraticCurveTo(sd * 22, -16 - w * 14, sd * 38, -8 - w * 22);
      c.quadraticCurveTo(sd * 30, 2 - w * 8, sd * 27, 7 - w * 8); c.quadraticCurveTo(sd * 20, 0, sd * 14, 8 - w * 4); c.quadraticCurveTo(sd * 8, 4, sd * 4, 9); c.closePath(); c.fill(); c.stroke();
    });
    c.beginPath(); c.ellipse(0, 3, 8, 10, 0, 0, TAU); c.fill(); c.stroke();
    c.beginPath(); c.ellipse(0, -9, 7.5, 7, 0, 0, TAU); c.fill(); c.stroke();
    [-1, 1].forEach(function (sd) { c.beginPath(); c.moveTo(sd * 3, -14); c.lineTo(sd * 8, -23); c.lineTo(sd * 8.5, -12); c.closePath(); c.fill(); c.stroke(); });
    c.fillStyle = mood === 'calm' ? '#a58' : '#ff3a3a'; c.beginPath(); c.arc(-3, -9, 2, 0, TAU); c.arc(3, -9, 2, 0, TAU); c.fill();
    if (mood !== 'calm') { c.fillStyle = '#fff'; c.beginPath(); c.moveTo(-3, -4); c.lineTo(-1.5, -1); c.lineTo(0, -4); c.moveTo(0, -4); c.lineTo(1.5, -1); c.lineTo(3, -4); c.fill(); }
    c.restore();
  }

  function drawBoneHand(c, th, x0, y0, x1, y1, grip, T, gone) {
    // an arm of bones from a crack at (x0, y0) to a hand at (x1, y1) (screen px); grip 0..1 curls the fingers
    var dx = x1 - x0, dy = y1 - y0, L = Math.sqrt(dx * dx + dy * dy) || 1, a = Math.atan2(dy, dx);
    c.save(); c.lineCap = 'round'; c.lineJoin = 'round';
    c.strokeStyle = 'rgba(20,6,6,.9)'; c.lineWidth = 11; c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke();
    c.strokeStyle = th.bone; c.lineWidth = 7; c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke();
    // joints along the arm
    for (var i = 1; i < 4; i++) { c.fillStyle = th.bone2; c.beginPath(); c.arc(x0 + dx * i / 4, y0 + dy * i / 4, 5.2, 0, TAU); c.fill(); }
    c.translate(x1, y1); c.rotate(a);
    c.fillStyle = th.bone; c.strokeStyle = 'rgba(20,6,6,.9)'; c.lineWidth = 2.4;
    c.beginPath(); c.ellipse(4, 0, 9, 7, 0, 0, TAU); c.fill(); c.stroke();
    for (var f = -1.5; f <= 1.5; f += 1) {                // four fingers
      var cur = grip * 1.0, base = f * 0.42 * (1 - grip * 0.55);
      c.save(); c.rotate(base);
      c.beginPath(); c.moveTo(8, 0); c.lineTo(8 + 8, 0); c.lineTo(8 + 8 + 7 * Math.cos(cur), 7 * Math.sin(cur) * (f < 0 ? -1 : 1) * 0.8); c.stroke();
      c.strokeStyle = th.bone; c.lineWidth = 4.4; c.beginPath(); c.moveTo(8, 0); c.lineTo(8 + 9, 0); c.lineTo(8 + 9 + 7 * Math.cos(cur), 7 * Math.sin(cur) * (f < 0 ? -1 : 1) * 0.8); c.stroke();
      c.strokeStyle = 'rgba(20,6,6,.9)'; c.lineWidth = 2.4;
      c.restore();
    }
    c.restore();
  }

  // ==================================================================== event props on the wall
  DRAW.props = function (I, c, sc, T) {
    var tl = sc.tl, th = I.th, cam = I.cam, tms = sc.tms;
    if (!tl) return;
    I_TH = th;
    for (var i = 0; i < tl.props.length; i++) {
      var pr = tl.props[i], b = pr.b, ys = ANCHOR_Y - ((pr.lv * LV) - cam.y);
      if (ys < -220 || ys > VH + 220) continue;
      var u = pr.decoy ? -9 : (tms - b.t) / b.d;
      switch (pr.type) {
        case 'demon': {
          var x = LANE_X + (b.side > 0 ? 196 : -178), y = ys - LV * 0.9;
          var talk = u > 0.02 && u < 0.62 ? 1 : 0, pt = u > 0.1 && u < 0.5 ? (b.side > 0 ? -1 : 1) : 0;
          var leaving = u > 1.1 ? easeIn((u - 1.1) / 0.5) : 0;
          if (leaving >= 1) break;
          // he looks out of a hole in the wall until the soul comes, then sits out on the ledge
          drawDemon(c, x + (b.side > 0 ? leaving * 160 : -leaving * 160), y, b.kind, { talk: talk, point: pt * (b.side > 0 ? 1 : -1), stand: talk ? 0.5 : 0, scale: 1.1, score: String(1 + (b.s % 4)) }, T);
          break;
        }
        case 'bats': DRAW.bats(I, c, sc, pr, u, ys, T); break;
        case 'vent': DRAW.vent(I, c, sc, pr, u, ys, T); break;
        case 'hand': {                                          // a skeleton hand waiting in a crack
          if (u > 0.05 && u < 1) break;                         // (the live one is drawn with the soul)
          var hx = LANE_X - 62, hy = ys + 8;
          if (u >= 1) break;
          c.fillStyle = '#05020a'; c.beginPath(); c.ellipse(hx - 12, hy, 22, 11, -0.2, 0, TAU); c.fill();
          c.strokeStyle = 'rgba(20,6,6,.9)'; c.lineWidth = 2;
          for (var f = 0; f < 3; f++) { var fy = Math.sin(T / 700 + f * 2) * 1.5; c.fillStyle = th.bone; c.beginPath(); c.ellipse(hx + 4 + f * 0.5, hy - 6 + f * 6 + fy, 8, 2.8, 0.1 * f, 0, TAU); c.fill(); c.stroke(); }
          break;
        }
        case 'spur': DRAW.spur(I, c, sc, pr, u, ys, T); break;
        case 'fray': DRAW.fray(I, c, sc, pr, u, ys, T); break;
        default: break;
      }
    }
  };

  DRAW.bats = function (I, c, sc, pr, u, ys, T) {
    var b = pr.b, th = I.th, n = b.n || 3, bx = LANE_X + (b.s & 1 ? 120 : -130), by = ys - LV * 1.8;
    if (u < 0) {                                              // asleep, hanging from a little ledge
      slab(c, bx, by - 14, 70, 11, th, '#4a3a42');
      for (var i = 0; i < Math.min(n, 4); i++) {
        c.save(); c.translate(bx - 24 + i * 16, by - 4); c.scale(1, -1); drawBat(c, 0, 0, Math.sin(T / 900 + i) * 0.2, 0.62, 'calm'); c.restore();
      }
      return;
    }
    if (u > 1.0) return;
    // the swarm: they come off the ledge, circle him, and flap away
    var p = I.soulScreen || { x: LANE_X, y: ANCHOR_Y - 60 };
    for (var j = 0; j < n; j++) {
      var arr = smooth(0, 0.14, u), leave = smooth(0.84, 1, u), ang = T / 120 * (1 + j * 0.17) + j * 2.1, rad = 56 + 24 * Math.sin(T / 300 + j * 2);
      var tx = p.x + Math.cos(ang) * rad * 1.15, ty = p.y - 58 + Math.sin(ang * 1.3) * rad * 0.62;
      var x = lerp(bx, tx, arr) + leave * (j % 2 ? 260 : -260), y = lerp(by, ty, arr) - leave * 340;
      drawBat(c, x, y, T / 55 + j, 1.05 + 0.12 * (j % 2), 'attack');
    }
  };

  DRAW.vent = function (I, c, sc, pr, u, ys, T) {
    var b = pr.b, th = I.th, vx = LANE_X + (b.side || 1) * 112, vy = ys + 2, warn = clamp((u + 1.2) / 1.2, 0, 1) * (u < 0.22 ? 1 : 0) + (u >= 0.22 ? 0.4 : 0);
    // the crack, glowing more as he gets near
    c.save(); c.translate(vx, vy);
    var glow = (u > -2 ? 0.18 + 0.2 * Math.abs(Math.sin(T / 300)) + warn * 0.35 : 0.1);
    var gr = c.createRadialGradient(0, 0, 3, 0, 0, 70); gr.addColorStop(0, 'rgba(' + th.glow + ',' + glow + ')'); gr.addColorStop(1, 'rgba(' + th.glow + ',0)');
    c.fillStyle = gr; c.fillRect(-80, -80, 160, 160);
    c.fillStyle = '#05020a'; c.beginPath(); c.moveTo(-4, -26); c.lineTo(3, -10); c.lineTo(-2, 4); c.lineTo(6, 22); c.lineTo(0, 24); c.lineTo(-7, 6); c.lineTo(-1, -9); c.lineTo(-8, -22); c.closePath(); c.fill();
    c.strokeStyle = th.lava1; c.lineWidth = 2.6; c.beginPath(); c.moveTo(-2, -22); c.lineTo(1, -9); c.lineTo(-3, 5); c.lineTo(3, 20); c.stroke();
    c.restore();
    if (u > 0.2 && u < 0.92) {                                // the jet: a fountain of lava
      var k = (u - 0.2) / 0.72, hgt = 270 * Math.sin(clamp(k * 1.25, 0, 1) * Math.PI * 0.62) * (1 - smooth(0.7, 1, k) * 0.9), x, y;
      c.save(); c.translate(vx, vy);
      var jg = c.createLinearGradient(0, 0, 0, -hgt); jg.addColorStop(0, th.lava2); jg.addColorStop(0.4, th.lava1); jg.addColorStop(1, th.lava0);
      var wv = Math.sin(T / 45) * 3, w0 = 22, w1 = 14;
      c.beginPath(); c.moveTo(-w0 - 4, 10);
      for (y = 0; y <= 1.001; y += 0.1) c.lineTo(-lerp(w0, w1, y) - Math.sin(y * 9 + T / 60) * 2.5 + wv * y, -hgt * y);
      c.quadraticCurveTo(wv, -hgt - 26, lerp(w0, w1, 1) + wv, -hgt);
      for (y = 1; y >= -0.001; y -= 0.1) c.lineTo(lerp(w0, w1, y) + Math.sin(y * 9 + T / 70) * 2.5 + wv * y, -hgt * y);
      c.lineTo(w0 + 4, 10); c.closePath();
      c.fillStyle = jg; c.fill(); c.lineWidth = 3; c.strokeStyle = 'rgba(90,16,2,.85)'; c.stroke();
      c.fillStyle = th.lava2; c.globalAlpha = 0.85; c.beginPath(); c.moveTo(-6, 6); c.quadraticCurveTo(-8, -hgt * 0.5, wv * 0.5, -hgt * 0.82); c.quadraticCurveTo(8, -hgt * 0.5, 6, 6); c.closePath(); c.fill(); c.globalAlpha = 1;
      var gl = c.createRadialGradient(0, -hgt * 0.4, 6, 0, -hgt * 0.4, 150); gl.addColorStop(0, 'rgba(' + th.glow + ',.35)'); gl.addColorStop(1, 'rgba(' + th.glow + ',0)');
      c.fillStyle = gl; c.fillRect(-160, -hgt * 0.4 - 160, 320, 320);
      c.restore();
      burst(c, vx, vy - hgt * 0.9, (u - 0.2) * b.d * 0.95, { n: 30, seed: b.s, speed: 250, g: 700, life: 1100, size: 3.8, colors: [th.lava1, th.lava2, th.lava0], spread: 2.4, shrink: true });
    }
  };

  DRAW.spur = function (I, c, sc, pr, u, ys, T) {
    // the dead end he'll try: a little staircase of ledges up the side, ending in spikes (and a sign)
    var b = pr.b, th = I.th, sd = b.side || 1, base = pr.lv, peak = b.peak, x0 = LANE_X + sd * 82;
    if (u > 1.4) { }
    for (var L = base; L <= peak; L++) {
      var p = W2S(I, sd * 82, L * LV);
      if (p.y < -40 || p.y > VH + 40) continue;
      slab(c, p.x + (L % 2 ? 8 : -8), p.y, 44, 11, th, '#5a4650');
    }
    var tp = W2S(I, sd * 82, (peak + 0.82) * LV);
    if (tp.y < -80 || tp.y > VH + 60) return;
    c.fillStyle = '#8c8794'; c.strokeStyle = 'rgba(10,2,4,.9)'; c.lineWidth = 2.2;
    for (var q = -2; q <= 2; q++) { c.beginPath(); c.moveTo(tp.x + q * 14 - 8, tp.y + 14); c.lineTo(tp.x + q * 14, tp.y - 22); c.lineTo(tp.x + q * 14 + 8, tp.y + 14); c.closePath(); c.fill(); c.stroke(); }
    c.fillStyle = '#7a4d28'; c.beginPath(); rr(c, tp.x + sd * 58 - 36, tp.y - 40, 72, 28, 4); c.fill(); c.stroke();
    c.fillStyle = '#ffe9b8'; c.font = '900 12px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('DEAD END', tp.x + sd * 58, tp.y - 26); c.textBaseline = 'alphabetic';
  };

  DRAW.fray = function (I, c, sc, pr, u, ys, T) {
    var th = I.th, b = pr.b, wy = pr.lv * LV + 98, p = W2S(I, 0, wy), sw = sway('rope', wy, T);
    if (p.y < -30 || p.y > VH + 30) return;
    var x = LANE_X + sw, snapped = u > 0.3, i;
    // a worn spot: the rope narrows, strands stick out
    c.fillStyle = 'rgba(10,2,4,.9)'; c.beginPath(); c.ellipse(x, p.y, 11, 15, 0, 0, TAU); c.fill();
    c.fillStyle = th.rope2; c.beginPath(); c.ellipse(x, p.y, 6.4, 13, 0, 0, TAU); c.fill();
    c.strokeStyle = th.rope; c.lineWidth = 2.4; c.lineCap = 'round';
    for (i = 0; i < 7; i++) {                                  // loose strands
      var r = hash2(b.s, i), dir = i % 2 ? -1 : 1, len = 10 + r * 12 + (snapped ? 8 : 0);
      c.beginPath(); c.moveTo(x + dir * 3, p.y - 12 + i * 4); c.quadraticCurveTo(x + dir * (len * 0.6), p.y - 8 + i * 4 + Math.sin(T / 150 + i) * 2, x + dir * len, p.y + 4 + i * 4 + (snapped ? 8 : 0)); c.stroke();
    }
    if (snapped && u < 1) {
      var age = (u - 0.3) * b.d;
      ring(c, x, p.y, age, 420, 4, 30, 'rgba(255,240,200,.95)', 3);
      burst(c, x, p.y, age, { n: 10, seed: b.s, speed: 130, g: 400, life: 600, size: 2.6, colors: [th.rope, '#fff', '#ffd9a0'], spread: 5.8, dir: 0 });
      textPop(c, th, 'PING!', x + 34, p.y - 22, age, 800, '#fff2c8', 22, 0.1);
    }
  };

  // what the soul is dragged into / the things around him in the last beats (cause of the fall, flailing bits ...)
  // a puff of dust and grit where he hits something (age in ms)
  function dustPuff(c, x, y, age, seed) {
    if (age < 0 || age > 900) return;
    var f = age / 900, i;
    for (i = 0; i < 7; i++) {
      var r1 = hash(seed * 7 + i), a = (i / 7) * Math.PI + (r1 - 0.5) * 0.5 + Math.PI, d = (10 + 38 * easeOut(f)) * (0.6 + r1), rad = 7 + 12 * f + r1 * 6;
      var px = x + Math.cos(a) * d * 1.3, py = y + Math.sin(a) * d * 0.55 - 6 * f, g = c.createRadialGradient(px, py, 1, px, py, rad);
      g.addColorStop(0, 'rgba(170,150,132,' + (0.5 * (1 - f)).toFixed(3) + ')'); g.addColorStop(1, 'rgba(170,150,132,0)');
      c.fillStyle = g; c.fillRect(px - rad, py - rad, rad * 2, rad * 2);
    }
  }

  DRAW.soulFx = function (I, c, sc, pose, sp, T) {
    var fx = pose.fx, th = I.th, tl = sc.tl, tms = sc.tms;
    if (!fx) return;
    if (fx.type === 'fall' && fx.style === 'bonk' && fx.bonk && fx.u <= fx.imp + 0.1) {
      var bk = fx.bonk; if (bk.k > 0 || bk.f > 0.2) dustPuff(c, sp.x, sp.y + 34, bk.f * 430, bk.k + 3);
    }
    if (fx.type === 'fall' && fx.stars) dustPuff(c, sp.x, sp.y + 12, (fx.u - fx.imp) * 5000, 9);
    if (fx.type === 'hand') {
      var hp = W2S(I, fx.x, fx.y), from = W2S(I, -92, tl.beats[tl.index(tms)].lv * LV - 8);
      if (fx.gone < 1) drawBoneHand(c, th, from.x, from.y, hp.x, hp.y, 0.3 + fx.held * 0.7, T);
    } else if (fx.type === 'fatal') {
      var u = fx.u, b = tl.fatalBeat, age = (tms - b.t);
      var hp2 = sp;
      if (fx.cause === 'hand' && fx.hand) {
        var hh = W2S(I, fx.hand.x, fx.hand.y), fr = W2S(I, -92, b.lv * LV - 8);
        drawBoneHand(c, th, fr.x, fr.y, hh.x, hh.y, 0.2 + fx.hand.held * 0.8, T);
      } else if (fx.cause === 'rope' || fx.cause === 'chain' || fx.cause === 'rib') {
        var pc = W2S(I, fx.piece.x, fx.piece.y);
        if (fx.break > 0) {
          if (fx.cause === 'rope') {
            c.strokeStyle = 'rgba(10,2,4,.9)'; c.lineWidth = 15; c.lineCap = 'round'; c.beginPath(); c.moveTo(pc.x, pc.y + 8); c.quadraticCurveTo(pc.x + 8, pc.y - 30, pc.x - 4, pc.y - 70 + Math.sin(T / 120) * 4); c.stroke();
            c.strokeStyle = th.rope; c.lineWidth = 10.5; c.beginPath(); c.moveTo(pc.x, pc.y + 8); c.quadraticCurveTo(pc.x + 8, pc.y - 30, pc.x - 4, pc.y - 70 + Math.sin(T / 120) * 4); c.stroke();
          } else if (fx.cause === 'chain') {
            for (var l = 0; l < 5; l++) link(c, pc.x + Math.sin(T / 150 + l) * 3, pc.y - 12 * l, 0, l % 2 ? 6 : 11, l % 2 ? 15 : 12, th, l % 2);
          } else {
            c.strokeStyle = 'rgba(30,10,10,.9)'; c.lineWidth = 12; c.lineCap = 'round'; c.beginPath(); c.moveTo(pc.x - 4, pc.y + 6); c.lineTo(pc.x + 8, pc.y - 52); c.stroke();
            c.strokeStyle = th.bone; c.lineWidth = 8; c.beginPath(); c.moveTo(pc.x - 4, pc.y + 6); c.lineTo(pc.x + 8, pc.y - 52); c.stroke();
          }
          var bt = (u - fx.rel) * b.d;
          burst(c, pc.x, pc.y - 10, bt + 40, { n: 14, seed: b.s, speed: 150, g: 500, life: 700, size: 3, colors: [th.rope, th.bone, '#fff'], spread: 5, dir: -Math.PI / 2 });
          ring(c, pc.x, pc.y + 6, bt, 450, 4, 34, 'rgba(255,240,210,.9)', 3);
        }
      } else if (fx.cause === 'geyser' && u > fx.rel - 0.06) {
        var gx = sp.x + fx.side * 54, gy = sp.y + 60, ga = (u - (fx.rel - 0.06)) * b.d;
        var jh = 210 * Math.sin(clamp(ga / 520, 0, 1) * Math.PI * 0.55);
        var gg = c.createLinearGradient(0, gy, 0, gy - jh); gg.addColorStop(0, th.lava2); gg.addColorStop(0.5, th.lava1); gg.addColorStop(1, th.lava0);
        c.fillStyle = gg; c.beginPath(); c.moveTo(gx - 12, gy + 120); c.quadraticCurveTo(gx - 16, gy - jh * 0.4, gx, gy - jh); c.quadraticCurveTo(gx + 16, gy - jh * 0.4, gx + 12, gy + 120); c.closePath(); c.fill();
        burst(c, gx, gy - jh * 0.6, ga, { n: 24, seed: b.s + 5, speed: 260, g: 800, life: 1000, size: 3.5, colors: [th.lava1, th.lava2, th.lava0], spread: 2.6, shrink: true });
      } else if (fx.cause === 'bat') {
        var nb = 4;
        for (var q = 0; q < nb; q++) {
          var a2 = T / 110 * (1 + q * 0.2) + q * 1.7, rd = 52 + 22 * Math.sin(T / 280 + q);
          drawBat(c, sp.x + Math.cos(a2) * rd * 1.1, sp.y - 56 + Math.sin(a2 * 1.3) * rd * 0.6 - smooth(fx.rel - 0.1, 1, u) * 30, T / 50 + q, 1.1, 'attack');
        }
      } else if (fx.cause === 'demon' && fx.demon) {
        var dd = fx.demon, side = fx.side, dx = sp.x + side * 96, dy = sp.y + 30 - 14 * (1 - dd.appear);
        if (dd.appear > 0.02) {
          c.save(); c.globalAlpha = clamp(dd.appear * 1.6, 0, 1);
          drawDemon(c, dx, dy + 56, 1, { talk: dd.d2 < 1 ? 0.7 : 0, point: 0, stand: 0, scale: 0.95 }, T);
          c.restore();
        }
      } else if (fx.cause === 'rock' && u > fx.rel - 0.04) {
        burst(c, sp.x, sp.y - 40, (u - (fx.rel - 0.04)) * b.d, { n: 18, seed: b.s + 9, speed: 120, g: 900, life: 900, size: 4, colors: ['#9a7f74', '#6b5650', '#cdbcb0'], spread: 3, dir: Math.PI / 2 });
      } else if (fx.cause === 'tired') {
        // little "…" and a sag
      }
    }
  };

  // ==================================================================== timeline extras: props, bubbles
  // The script says what happens when; this lists the things that stand on the wall because of it (a demon on his
  // ledge, sleeping bats, a vent, a skeleton hand in a crack, a dead end, a worn rope) and what is said, and when.
  TP._extras = function (name) {
    var beats = this.beats, seed = this.seed, props = this.props, bubbles = this.bubbles, lastEnd = -1e9, i, b, r;
    function say(t0, text, who, dur, extra) {
      if (!text) return;
      text = fillName(text, name);
      if (who === 'soul' && t0 < lastEnd + 300) return;                 // never talks over himself
      dur = dur || clamp(950 + text.length * 55, 1400, 2800);
      var o = { t0: t0, t1: t0 + dur, text: text, who: who };
      if (extra) for (var k in extra) o[k] = extra[k];
      bubbles.push(o);
      if (who === 'soul') lastEnd = t0 + dur;
    }
    function line(cat, rr2) { var a = LINES[cat]; return a ? pick(a, rr2) : null; }
    for (i = 0; i < beats.length; i++) {
      b = beats[i]; r = hash2(b.s, seed);
      switch (b.ev) {
        case 'ready': say(b.t + b.d * 0.4, line('ready', r), 'soul', 1800); break;
        case 'climb': if (r < 0.075) say(b.t + 50, line('effort', hash2(b.s, 77)), 'soul'); break;
        case 'cheer': say(b.t + 40, line('cheer', r), 'soul', 1300); break;
        case 'idle': say(b.t + b.d * 0.1, line('idle_' + b.kind, r), 'soul', Math.max(1300, b.d)); break;
        case 'rest': say(b.t + b.d * 0.22, line('rest', r), 'soul'); break;
        case 'taunt':
          props.push({ type: 'demon', lv: b.lv, b: b });
          say(b.t + b.d * 0.06, line('taunt', r), 'demon', b.d * 0.56, { side: b.side, lv: b.lv });
          say(b.t + b.d * 0.64, line('taunt_reply', hash2(b.s, 5)), 'soul', b.d * 0.34);
          break;
        case 'slip': this.loose[b.lv] = b.t + b.d * 0.1; this.crack[b.lv] = true; say(b.t + 40, line('slip', r), 'soul', 1400); break;
        case 'bat': props.push({ type: 'bats', lv: b.lv, b: b }); say(b.t + b.d * 0.12, line('bat', r), 'soul'); break;
        case 'geyser': props.push({ type: 'vent', lv: b.lv, b: b }); say(b.t + b.d * 0.3, line('geyser', r), 'soul'); break;
        case 'grab': props.push({ type: 'hand', lv: b.lv, b: b }); say(b.t + b.d * 0.3, line('grab', r), 'soul'); break;
        case 'deadend': props.push({ type: 'spur', lv: b.lv, b: b }); say(b.t + b.d * 0.5, line('deadend', r), 'soul'); break;
        case 'fray': props.push({ type: 'fray', lv: b.lv, b: b }); say(b.t + b.d * 0.12, line('fray', r), 'soul'); break;
        case 'fatal': {
          var rel = FATAL_RELEASE[b.cause] || 0.6;
          if (b.cause === 'rock') { this.loose[b.lv] = b.t + b.d * rel; this.crack[b.lv] = true; }
          say(b.t + b.d * 0.06, line('fatal_' + b.cause, r), 'soul', b.d * 0.86);
          break;
        }
        case 'fall': {
          var imp = FALL_IMPACT[b.style] || 0.55;
          say(b.t + 80, line('fall', r), 'soul', Math.min(2200, b.d * 0.4));
          var cat = { bonk: 'bonk', yelp: 'splash', cauldron: 'splash', flick: 'flick', grinder: 'grinder', boing: 'boing', umbrella: 'umbrella' }[b.style];
          say(b.t + b.d * (imp + 0.04), line(cat, hash2(b.s, 9)), b.style === 'flick' ? 'demon' : 'soul', 1700, b.style === 'flick' ? { side: 1, lv: 0, fin: 1 } : null);
          break;
        }
        case 'escape': say(b.t + b.d * 0.52, line('escape', r), 'soul', 2600); break;
        default: break;
      }
    }
    // Decoys: the same waiting props ABOVE his best height, never used. The wall then looks alike wherever he will stop
    // (no sleeping bats and demons only below the place where he falls), so nothing on it gives the end away.
    var rd = mulberry(((hash2(seed, 90210) * 4294967296) | 0) || 1), topLv = Math.min(this.H, this.max + 64), lv = this.max + 1 + Math.floor(rd() * 4);
    var kinds = ['demon', 'bats', 'vent', 'hand', 'spur', 'fray'], wts = [3, 1.5, 2, 2, 1.5, 1.5], routes = this.script.routes || [];
    function routeKind(l) { for (var k = 0; k < routes.length; k++) if (routes[k].from <= l && l < routes[k].to) return routes[k].kind; return null; }
    while (lv < topLv) {
      var pickW = rd() * 11.5, ty = kinds[0], acc = 0, kk, skip = rd() > 0.62;      // (only about 6 in 10 of the real events leave a prop)
      if (skip) { lv += 5 + Math.floor(rd() * 6); continue; }
      for (kk = 0; kk < kinds.length; kk++) { acc += wts[kk]; if (pickW <= acc) { ty = kinds[kk]; break; } }
      if (ty === 'fray' && routeKind(lv) !== 'rope') ty = 'vent';
      props.push({ type: ty, lv: lv, decoy: true, b: { t: 1e12, d: 1, ev: 'decoy', lv: lv, to: lv, s: (rd() * 2147483647) | 0, side: rd() < 0.5 ? -1 : 1,
        kind: Math.floor(rd() * 3), n: 2 + Math.floor(rd() * 4), peak: lv + 2 + Math.floor(rd() * 3) } });
      lv += 5 + Math.floor(rd() * 6);
    }
  };
  TP.bubbleAt = function (t) {
    var out = [];
    for (var i = 0; i < this.bubbles.length; i++) { var q = this.bubbles[i]; if (t >= q.t0 && t <= q.t1) out.push(q); }
    return out;
  };

  // ==================================================================== sound (synthesised: no files)
  function Sfx() { this.ctx = null; this.vol = 0.6; }
  Sfx.prototype.ac = function () {
    if (!this.ctx) {
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) return null;
      try { this.ctx = new AC(); } catch (e) { return null; }
    }
    if (this.ctx.state === 'suspended') { try { this.ctx.resume(); } catch (e) {} }
    return this.ctx;
  };
  Sfx.prototype._buf = function (c) {
    if (this._noise) return this._noise;
    var b = c.createBuffer(1, c.sampleRate, c.sampleRate), d = b.getChannelData(0);
    for (var i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    return (this._noise = b);
  };
  Sfx.prototype.noise = function (dur, type, f0, f1, q, gain, delay) {
    var c = this.ac(); if (!c) return;
    var t = c.currentTime + (delay || 0);
    var s = c.createBufferSource(); s.buffer = this._buf(c);
    var f = c.createBiquadFilter(); f.type = type; f.frequency.setValueAtTime(f0, t);
    if (f1) f.frequency.exponentialRampToValueAtTime(f1, t + dur);
    f.Q.value = q || 1;
    var g = c.createGain(); g.gain.setValueAtTime(gain * this.vol, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    s.connect(f); f.connect(g); g.connect(c.destination);
    s.start(t, Math.random() * 0.5); s.stop(t + dur + 0.02);
  };
  Sfx.prototype.tone = function (freq, dur, gain, type, to, delay, lp, vib) {
    var c = this.ac(); if (!c) return;
    var t = c.currentTime + (delay || 0);
    var o = c.createOscillator(); o.type = type || 'sine'; o.frequency.setValueAtTime(freq, t);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t + dur);
    var g = c.createGain(); g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(gain * this.vol, t + Math.min(0.012, dur / 3));
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    var out = g;
    if (lp) { var f = c.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = lp; f.Q.value = 2.5; o.connect(f); f.connect(g); } else o.connect(g);
    if (vib) { var l = c.createOscillator(), lg = c.createGain(); l.frequency.value = vib[0]; lg.gain.value = vib[1]; l.connect(lg); lg.connect(o.frequency); l.start(t); l.stop(t + dur + 0.02); }
    out.connect(c.destination); o.start(t); o.stop(t + dur + 0.02);
  };
  Sfx.prototype.play = function (name) {
    var r = Math.random;
    switch (name) {
      case 'step': this.noise(0.045, 'bandpass', 700 + r() * 500, 0, 1.4, 0.16); break;
      case 'grunt': var f = 130 + r() * 70; this.tone(f, 0.17, 0.4, 'sawtooth', f * 0.7, 0, 900); this.noise(0.08, 'bandpass', 800, 0, 1, 0.12); break;
      case 'ding': this.tone(1318, 0.34, 0.26, 'sine'); this.tone(1760, 0.4, 0.22, 'sine', 0, 0.09); this.noise(0.12, 'highpass', 6000, 0, 1, 0.12, 0.05); break;
      case 'gulp': this.tone(240, 0.12, 0.4, 'sine', 110); this.noise(0.03, 'bandpass', 2400, 0, 3, 0.2, 0.11); break;
      case 'sigh': this.noise(0.55, 'lowpass', 1400, 300, 0.7, 0.2); break;
      case 'taunt': this.tone(140, 0.14, 0.34, 'sawtooth', 100, 0, 700); this.tone(150, 0.14, 0.34, 'sawtooth', 105, 0.2, 700); this.tone(160, 0.2, 0.34, 'sawtooth', 100, 0.4, 700); break;
      case 'reply': this.tone(300, 0.1, 0.3, 'square', 220, 0, 900); break;
      case 'slip': this.tone(1000, 0.5, 0.22, 'triangle', 220); this.noise(0.25, 'bandpass', 1800, 700, 1, 0.2); break;
      case 'catch': this.tone(110, 0.2, 0.7, 'sine', 50); this.noise(0.1, 'lowpass', 600, 0, 1, 0.3); break;
      case 'bats': for (var i = 0; i < 14; i++) this.noise(0.05, 'bandpass', 2600 + r() * 900, 0, 4, 0.14, i * 0.075); break;
      case 'rumble': this.noise(0.9, 'lowpass', 160, 70, 0.8, 0.5); break;
      case 'geyser': this.noise(1.0, 'highpass', 1400, 500, 0.7, 0.4); this.noise(0.8, 'lowpass', 900, 200, 0.6, 0.4, 0.05); break;
      case 'clatter': for (var j = 0; j < 7; j++) this.noise(0.03, 'bandpass', 3000 + r() * 2000, 0, 8, 0.3, j * 0.045 + r() * 0.03); break;
      case 'creak': this.tone(85, 0.55, 0.3, 'square', 110, 0, 420, [9, 12]); break;
      case 'snap': this.noise(0.05, 'highpass', 3000, 0, 1, 0.8); this.tone(1900, 0.1, 0.25, 'square', 300); break;
      case 'ouch': this.tone(360, 0.2, 0.4, 'sawtooth', 200, 0, 1200); break;
      case 'thud': this.tone(95, 0.28, 0.9, 'sine', 40); this.noise(0.12, 'lowpass', 500, 0, 1, 0.5); break;
      case 'bonk': this.tone(540, 0.12, 0.5, 'square', 320, 0, 2200); this.noise(0.04, 'bandpass', 1800, 0, 3, 0.4); this.tone(110, 0.14, 0.5, 'sine', 60); break;
      case 'whistle': this.tone(1700, 1.0, 0.22, 'sine', 260); break;
      case 'splash': this.noise(0.6, 'bandpass', 1500, 250, 0.9, 0.7); this.tone(80, 0.3, 0.7, 'sine', 40); for (var q = 0; q < 5; q++) this.tone(300 + r() * 500, 0.07, 0.2, 'sine', 0, 0.2 + q * 0.09); break;
      case 'sizzle': this.noise(0.8, 'highpass', 4000, 2000, 0.6, 0.18); break;
      case 'boing': this.tone(180, 0.5, 0.5, 'sine', 560, 0, 0, [14, 70]); this.tone(560, 0.3, 0.4, 'sine', 200, 0.4, 0, [12, 40]); break;
      case 'flick': this.noise(0.08, 'highpass', 2500, 0, 1, 0.5); this.tone(900, 0.28, 0.3, 'triangle', 2800, 0.05); this.tone(2600, 0.5, 0.22, 'sine', 3400, 0.4); break;
      case 'grind': this.tone(70, 1.2, 0.5, 'sawtooth', 62, 0, 300, [11, 8]); this.noise(1.2, 'lowpass', 700, 300, 0.8, 0.35); break;
      case 'pop': this.noise(0.1, 'lowpass', 1200, 200, 1, 0.5); break;
      case 'fanfare': [523, 659, 784, 1047].forEach(function (fq, n) { this.tone(fq, 0.22, 0.34, 'triangle', 0, n * 0.12); }, this); this.tone(1047, 0.7, 0.3, 'triangle', 0, 0.5); this.tone(1568, 0.7, 0.2, 'sine', 0, 0.5); break;
      case 'sad': [[311, 0], [294, 0.3], [277, 0.6]].forEach(function (n) { this.tone(n[0], 0.28, 0.3, 'sawtooth', 0, n[1], 800, [6, 6]); }, this); this.tone(262, 0.9, 0.3, 'sawtooth', 185, 0.9, 800, [7, 9]); break;
      case 'beep': this.tone(880, 0.07, 0.18, 'square'); break;
      case 'win': this.tone(660, 0.14, 0.25, 'triangle'); this.tone(880, 0.14, 0.25, 'triangle', 0, 0.12); this.tone(1320, 0.3, 0.25, 'triangle', 0, 0.24); break;
      case 'poof': this.noise(0.4, 'lowpass', 900, 200, 0.8, 0.4); break;
      default: break;
    }
  };

  // ==================================================================== styles
  var CSS_DONE = false;
  function injectCss() {
    if (CSS_DONE || !root.document) return;
    CSS_DONE = true;
    var css = [
      '.hcl{position:absolute;inset:0;font-family:var(--hcl-font);color:var(--hcl-ink);pointer-events:none;}',
      '.hcl canvas{position:absolute;left:0;top:0;width:100%;height:100%;}',
      '.hcl-col{position:absolute;left:' + COL_X + 'px;top:0;width:' + COL_W + 'px;height:' + BASE_H + 'px;display:flex;flex-direction:column;gap:9px;}',
      '.hcl-title{padding:2px 4px 0;min-height:60px}',
      '.hcl-name{display:block;font-weight:900;font-size:27px;letter-spacing:.05em;text-transform:uppercase;color:var(--hcl-accent);text-shadow:0 2px 0 rgba(0,0,0,.6),var(--hcl-glow);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:6px 10px;margin:-6px -10px -4px}',
      '.hcl-sub{display:block;font-size:13px;letter-spacing:.14em;color:var(--hcl-dim);text-transform:uppercase;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.hcl-box{background:var(--hcl-panel);border:1px solid var(--hcl-line);border-radius:14px;padding:10px 12px;box-shadow:0 10px 30px rgba(0,0,0,.45)}',
      '.hcl-box h4{margin:0 0 6px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--hcl-accent)}',
      '.hcl-timer{display:flex;align-items:center;justify-content:space-between;padding:8px 14px}',
      '.hcl-timer b{display:block;font-size:11px;letter-spacing:.16em;color:var(--hcl-dim);text-transform:uppercase;font-weight:700}',
      '.hcl-timer span{font-size:34px;font-weight:900;line-height:1;font-variant-numeric:tabular-nums}',
      '.hcl-timer.last span{color:var(--hcl-bad);animation:hcl-pulse .5s ease-in-out infinite alternate}',
      '@keyframes hcl-pulse{from{transform:scale(1)}to{transform:scale(1.12)}}',
      '.hcl-meter{display:flex;gap:16px;height:440px;padding:26px 12px 12px 22px}',
      '.hcl-gauge{position:relative;width:34px;flex:0 0 34px;border-radius:17px;background:rgba(0,0,0,.5);border:2px solid var(--hcl-line);overflow:visible}',
      '.hcl-fill{position:absolute;left:3px;right:3px;bottom:3px;border-radius:14px;background:linear-gradient(0deg,var(--hcl-lava0),var(--hcl-lava1) 60%,var(--hcl-lava2));box-shadow:0 0 12px var(--hcl-glowc)}',
      '.hcl-gtop{position:absolute;left:50%;top:-1px;transform:translate(-50%,-100%);font-size:10px;font-weight:900;letter-spacing:.14em;color:var(--hcl-accent);white-space:nowrap;margin-top:-3px}',
      '.hcl-tick{position:absolute;left:-7px;right:-7px;height:4px;margin-bottom:-2px;border-radius:2px;background:var(--hcl-accent);box-shadow:0 0 0 1.5px rgba(10,2,6,.85)}',
      '.hcl-tick i{position:absolute;right:-9px;top:-3px;width:10px;height:10px;border-radius:50%;background:inherit;box-shadow:0 0 0 1.5px rgba(10,2,6,.85)}',
      '.hcl-tick.passed{background:var(--hcl-good)} .hcl-tick.lost{background:#8a8590;opacity:.7}',
      '.hcl-dot{position:absolute;left:50%;width:22px;height:22px;margin-left:-11px;margin-bottom:-11px;border-radius:50%;background:radial-gradient(circle at 35% 35%,#fff,var(--hcl-accent));border:3px solid #2a1230;box-shadow:0 0 12px var(--hcl-accent)}',
      '.hcl-read{flex:1;min-width:0;display:flex;flex-direction:column}',
      '.hcl-read .lbl{font-size:11px;letter-spacing:.16em;color:var(--hcl-dim);text-transform:uppercase;font-weight:700}',
      '.hcl-h{font-size:58px;font-weight:900;line-height:.95;font-variant-numeric:tabular-nums;color:var(--hcl-ink);text-shadow:var(--hcl-glow)}',
      '.hcl-h small{font-size:20px;color:var(--hcl-dim);margin-left:4px;text-shadow:none}',
      '.hcl-best{font-size:15px;color:var(--hcl-dim);margin:2px 0 12px}',
      '.hcl-best b{color:var(--hcl-ink)}',
      '.hcl-wv,.hcl-wv b{color:var(--hcl-accent)}',
      '.hcl-next{display:flex;flex-direction:column;gap:6px;margin-top:2px;overflow:hidden}',
      '.hcl-nf{display:flex;align-items:baseline;justify-content:space-between;gap:6px;padding:5px 8px;border-radius:9px;background:rgba(255,255,255,.06);border-left:4px solid var(--hcl-accent);font-size:14px}',
      '.hcl-nf.passed{border-left-color:var(--hcl-good);opacity:.75} .hcl-nf.lost{border-left-color:#8a8590;opacity:.5;text-decoration:line-through}',
      '.hcl-nf b{font-size:17px;font-variant-numeric:tabular-nums} .hcl-nf i{font-style:normal;font-weight:900;font-size:16px;color:var(--hcl-accent);font-variant-numeric:tabular-nums}',
      '.hcl-nf u{text-decoration:none;display:block;font-size:11px;color:var(--hcl-dim);max-width:104px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.hcl-players{flex:1;min-height:0;overflow:hidden}',
      '.hcl-more{float:right;letter-spacing:.06em;color:var(--hcl-dim);text-transform:none;font-weight:700}',
      '.hcl-p{display:flex;justify-content:space-between;gap:6px;font-size:14px;line-height:1.15;padding:3px 0;border-top:1px solid rgba(255,255,255,.07)}',
      '.hcl-p u{text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:104px;font-weight:700}',
      '.hcl-p i{font-style:normal;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right}',
      '.hcl-p small{display:block;color:var(--hcl-dim);font-size:10.5px;line-height:1.1;font-weight:600}',
      '.hcl-p .ok{color:var(--hcl-good)} .hcl-p .bad{color:var(--hcl-bad)} .hcl-p .dim{color:var(--hcl-dim)}',
      '.hcl-empty{color:var(--hcl-dim);font-size:13px;padding:4px 0}',
      '.hcl-rules{position:absolute;left:16px;top:16px;width:262px;padding:9px 11px;transition:opacity .35s}',
      '.hcl-rules p{margin:2px 0 5px;font-size:12.5px;line-height:1.28}',
      '.hcl-rules em{font-style:normal;font-weight:900;color:var(--hcl-accent)}',
      '.hcl-rules table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums;margin-top:3px}',
      '.hcl-rules th{font-size:10px;letter-spacing:.1em;color:var(--hcl-dim);font-weight:700;text-align:right;padding:0 3px 3px}',
      '.hcl-rules th:first-child,.hcl-rules td:first-child{text-align:left}',
      '.hcl-rules td{padding:1px 3px;text-align:right;border-top:1px solid rgba(255,255,255,.07)}',
      '.hcl-rules .cmd{margin-top:5px;font-size:12px;color:var(--hcl-dim)}',
      '.hcl-rules .warn{margin-top:5px;font-weight:900;color:var(--hcl-bad);font-size:15px;letter-spacing:.04em;animation:hcl-pulse .5s ease-in-out infinite alternate;transform-origin:left center}',
      '.hcl-banner{position:absolute;left:30px;width:540px;top:28px;text-align:center;transition:opacity .3s}',
      '.hcl-banner .card{background:var(--hcl-panel);border:2px solid var(--hcl-line);border-radius:18px;padding:14px 18px 12px;box-shadow:0 14px 40px rgba(0,0,0,.55)}',
      '.hcl-banner .kick{font-size:12px;letter-spacing:.22em;color:var(--hcl-dim);text-transform:uppercase;font-weight:800}',
      '.hcl-banner .big{font-weight:900;font-size:36px;line-height:1.05;margin:3px 0 2px;text-shadow:0 3px 0 rgba(0,0,0,.6),var(--hcl-glow);color:var(--hcl-accent)}',
      '.hcl-banner .big.win{color:var(--hcl-good)} .hcl-banner .big.bad{color:var(--hcl-bad)}',
      '.hcl-banner .why{font-size:15px;color:var(--hcl-ink);opacity:.9;margin-bottom:8px}',
      '.hcl-banner .rows{text-align:left;margin-top:6px}',
      '.hcl-banner .row{display:flex;justify-content:space-between;gap:10px;font-size:16px;padding:3px 4px;border-top:1px solid rgba(255,255,255,.08)}',
      '.hcl-banner .row i{font-style:normal;font-weight:900;font-variant-numeric:tabular-nums}',
      '.hcl-banner .row small{color:var(--hcl-dim);font-size:12px;margin-left:6px}',
      '.hcl-banner .pos{color:var(--hcl-good)} .hcl-banner .neg{color:var(--hcl-bad)}',
      '.hcl-banner .tot{display:flex;justify-content:center;gap:16px;margin-top:8px;font-size:13px;color:var(--hcl-dim)}',
      '.hcl-banner .none{color:var(--hcl-dim);font-size:14px;padding:6px 0 2px}',
      '.hcl-test{flex:none;text-align:right;padding:0 6px 2px;font-size:11px;letter-spacing:.2em;color:var(--hcl-bad);font-weight:900}',
      '.hcl-test:empty{display:none}',
      '.hcl-hide{opacity:0!important}'
    ].join('\n');
    var st = root.document.createElement('style');
    st.setAttribute('data-hexgames', 'climb');
    st.textContent = css;
    root.document.head.appendChild(st);
  }

  // ==================================================================== the instance
  function CL(container, config, opts) {
    injectCss();
    opts = opts || {};
    this.box = container;
    this.cfg = merge(DEFAULTS, config);
    this.sfx = opts.sound !== false ? new Sfx() : null;
    this.root = root.document.createElement('div');
    this.root.className = 'hcl';
    this.canvas = root.document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.root.appendChild(this.canvas);
    this.ui = root.document.createElement('div');
    this.root.appendChild(this.ui);
    container.appendChild(this.root);
    this.k = 1; this.st = null; this.at = now(); this.keys = {}; this.done = {};
    this.cam = { x: 0, y: FOOT, init: false };
    this.cache = {}; this.cacheList = [];
    this.tl = null; this.tlKey = null; this.H = 100; this.T0 = now(); this.lt = null;
    this.soulScreen = { x: LANE_X, y: ANCHOR_Y };
    this.th = THEMES.inferno;
    this._build();
    this.setConfig(this.cfg);
    this.resize();
    var self = this;
    this._loop = function (t) { if (self.dead) return; try { self._frame(t); } catch (e) { self._err(e); } self._raf = raf(self._loop); };
    this._raf = raf(this._loop);
  }
  var P = CL.prototype;
  P._err = function (e) {
    if (this._errAt && now() - this._errAt < 5000) return;
    this._errAt = now();
    if (root.console) console.error('[climb] frame error:', e);
  };

  P._build = function () {
    this.ui.innerHTML =
      '<div class="hcl-col">' +
        '<div class="hcl-title"><span class="hcl-name"></span><span class="hcl-sub"></span></div>' +
        '<div class="hcl-box hcl-timer"><b></b><span></span></div>' +
        '<div class="hcl-box hcl-meter"><div class="hcl-gauge"><div class="hcl-gtop">ESCAPE</div><div class="hcl-fill"></div><div class="hcl-ticks"></div><div class="hcl-dot"></div></div>' +
          '<div class="hcl-read"><div class="lbl">Height</div><div class="hcl-h"><span class="hcl-hv">0</span><small>/ 100</small></div><div class="hcl-best">best <b class="hcl-bv">0</b><span class="hcl-wv"></span></div>' +
          '<div class="lbl hcl-nl">Next flags</div><div class="hcl-next"></div></div></div>' +
        '<div class="hcl-box hcl-players"><h4 class="hcl-ph">On the line</h4><div class="hcl-pb"></div></div>' +
        '<div class="hcl-test"></div>' +
      '</div>' +
      '<div class="hcl-box hcl-rules"></div>' +
      '<div class="hcl-banner"></div>';
    var q = this.ui.querySelector.bind(this.ui);
    this.el = { name: q('.hcl-name'), sub: q('.hcl-sub'), timer: q('.hcl-timer'), tlabel: q('.hcl-timer b'), tval: q('.hcl-timer span'),
      meter: q('.hcl-meter'), fill: q('.hcl-fill'), ticks: q('.hcl-ticks'), dot: q('.hcl-dot'), gauge: q('.hcl-gauge'), gtop: q('.hcl-gtop'),
      hv: q('.hcl-hv'), hmax: q('.hcl-h small'), bv: q('.hcl-bv'), worth: q('.hcl-wv'), next: q('.hcl-next'), nl: q('.hcl-nl'), players: q('.hcl-players'),
      ph: q('.hcl-ph'), pb: q('.hcl-pb'), rules: q('.hcl-rules'), banner: q('.hcl-banner'), test: q('.hcl-test') };
  };

  P.setConfig = function (cfg) {
    this.cfg = merge(DEFAULTS, cfg);
    var th = THEMES[this.cfg.theme] || THEMES.inferno;
    var changed = th !== this.th;
    this.th = th; I_TH = th;
    var s = this.root.style;
    s.setProperty('--hcl-font', th.font); s.setProperty('--hcl-ink', th.ink); s.setProperty('--hcl-dim', th.dim);
    s.setProperty('--hcl-accent', th.accent); s.setProperty('--hcl-panel', th.panel); s.setProperty('--hcl-line', th.line);
    s.setProperty('--hcl-good', th.good); s.setProperty('--hcl-bad', th.bad); s.setProperty('--hcl-glow', th.textGlow);
    s.setProperty('--hcl-lava0', th.lava0); s.setProperty('--hcl-lava1', th.lava1); s.setProperty('--hcl-lava2', th.lava2);
    s.setProperty('--hcl-glowc', 'rgba(' + th.glow + ',.7)');
    var title = String(this.cfg.title == null ? '' : this.cfg.title).trim() || DEFAULTS.title;
    this.el.name.textContent = title; this.el.name.title = title;
    this.el.name.style.fontSize = (title.length <= 14 ? 27 : Math.max(15, Math.round(27 * 14 / title.length))) + 'px';
    if (this.sfx) this.sfx.vol = clamp(+this.cfg.sfx_volume || 0, 0, 1);
    if (changed) { this.cache = {}; this.cacheList = []; }
    this.keys = {};
  };

  P.resize = function () {
    var r = this.box.getBoundingClientRect ? this.box.getBoundingClientRect() : { width: BASE_W };
    var dpr = root.devicePixelRatio || 1;
    var w = r.width > 4 ? r.width : BASE_W;
    var k = Math.max(0.3, Math.min(2, w * dpr / BASE_W));          // (the canvas is never drawn at more than 2x: a big scene stays smooth, a little soft)
    var cw = Math.round(BASE_W * k), ch = Math.round(BASE_H * k);
    if (cw === this.canvas.width && ch === this.canvas.height && k === this.k) return;
    this.k = k; this.canvas.width = cw; this.canvas.height = ch;
    this.cache = {}; this.cacheList = [];
  };

  P.setState = function (st) {
    this.st = st && typeof st === 'object' ? st : null;
    this.at = now();
    var g = this.st && this.st.game, idle = this.st && this.st.idle;
    this.H = clamp(Math.round((g && g.height) || (idle && idle.height) || +this.cfg.max_height || 100), 2, 400);
    if (g && g.script && (g.phase === 'climbing' || g.phase === 'result' || g.phase === 'over')) {
      var key = g.id + '/' + g.climb + '/' + g.script.seed;
      if (key !== this.tlKey) {
        this.tl = new Timeline(g.script, g.soul && g.soul.name);
        this.tlKey = key; this.done = {}; this.cam.init = false;
      }
    } else if (this.tl) { this.tl = null; this.tlKey = null; this.cam.init = false; this.done = {}; }
    var sk = g ? g.id + '/' + g.climb + '/' + g.phase : 'idle';
    if (sk !== this._sceneKey) { this._sceneKey = sk; this._fadeAt = now(); }
  };
  P.reset = function () { this.st = null; this.tl = null; this.tlKey = null; this.keys = {}; this.done = {}; this.cam.init = false; };
  P.destroy = function () {
    this.dead = true; caf(this._raf);
    if (this.root.parentNode) this.root.parentNode.removeChild(this.root);
    if (this.sfx && this.sfx.ctx) { try { this.sfx.ctx.close(); } catch (e) {} }
  };

  P._elapsed = function (t) { var g = this.st && this.st.game; return g ? (+g.elapsed_ms || 0) + (t - this.at) : 0; };
  P._left = function (t) {
    var g = this.st && this.st.game;
    if (!g || g.ends_in_ms == null) return null;
    return Math.max(0, (+g.ends_in_ms || 0) - (t - this.at));
  };
  P._snd = function (name) { if (this.sfx && this.cfg.sfx) { try { this.sfx.play(name); } catch (e) {} } };
  P._cue = function (name, due, tnow, snd) {
    // a one-shot sound at script time `due` (ms): never replayed on a late join
    var key = (this.tlKey || 'x') + ':' + name;
    if (this.done[key] || tnow < due) return false;
    this.done[key] = 1;
    if (tnow - due < 500) { this._snd(snd || name); return true; }
    return false;
  };

  // ------------------------------------------------------------------ the frame
  P._frame = function (t) {
    var T = t - this.T0, dt = this.lt == null ? 0.016 : clamp((t - this.lt) / 1000, 0, 0.1);
    this.lt = t;
    var sc = this._scene(t, T);
    this._camera(sc, t, dt);
    this._draw(sc, t, T);
    this._dom(sc, t);
    this._audio(sc, t);
  };

  P._scene = function (t, T) {
    var st = this.st, g = st && st.game, ph = g ? g.phase : 'idle', cfg = this.cfg, tl = this.tl;
    var sc = { g: g, ph: ph, tl: null, tms: 0, pose: null, T: T, H: this.H, markers: g ? (g.markers || []) : [], level: 0, nerv: 0 };
    if (g && tl && (ph === 'climbing' || ph === 'result' || ph === 'over')) {
      var el = this._elapsed(t), D = tl.D, tms;
      if (ph === 'climbing') tms = el;
      else if (ph === 'result') tms = D + TAIL_MS + el;
      else tms = D + TAIL_MS + (+cfg.result_seconds || 8) * 1000 + el;
      sc.tl = tl; sc.tms = tms;
      var tp = Math.min(tms, D - 0.5);
      sc.pose = soulPose(tl, tp, T);
      sc.level = tl.level(tp);
    } else {
      var left = this._left(t);
      sc.nerv = g && ph === 'betting' && left != null ? clamp(1 - left / 12000, 0, 1) : 0;
      sc.pose = this._idlePose(T, sc.nerv);
    }
    return sc;
  };

  P._idlePose = function (T, nerv) {
    var p = standPose(T), cyc = (T / 1000) % 11;
    p.x = -118;
    p.hands = [{ x: p.x - 34, y: FOOT - 8 }, { x: p.x + 34, y: FOOT - 8 }];
    p.feet = [{ x: p.x - 15, y: 0 }, { x: p.x + 15, y: 0 }];
    p.mood = nerv > 0.5 ? 'scared' : 'calm'; p.mouth = 0.1 + nerv * 0.4; p.sweat = nerv; p.line = 'ready';
    p.look = { x: Math.sin(T / 1700) * 0.9, y: -0.2 + Math.sin(T / 2300) * 0.4 };
    if (cyc > 7 && cyc < 8.6 && nerv < 0.4) {
      p.hands[1] = { x: p.x + 38, y: FOOT + 78 + Math.sin(T / 95) * 14 }; p.grip = [0.4, 0]; p.mood = 'happy'; p.mouth = 0.6; p.look = { x: 0, y: 0 };
    } else if (cyc > 3 && cyc < 4.2 && nerv < 0.4) {
      p.hands[0] = { x: p.x - 16, y: FOOT + 78 }; p.hands[1] = { x: p.x + 16, y: FOOT + 78 }; p.mood = 'smug';
    }
    if (nerv > 0.3) { p.x += Math.sin(T / 28) * 1.6 * nerv; p.hands[0] = { x: p.x - 10, y: FOOT + 52 }; p.hands[1] = { x: p.x + 10, y: FOOT + 52 }; }
    return p;
  };

  // the camera follows him up the wall; during betting it takes a slow trip up past the flags and back
  P._camera = function (sc, t, dt) {
    var cam = this.cam, target, tau;
    if (sc.tl) {
      target = sc.pose.y;
      var fx = sc.pose.fx;
      tau = fx && fx.type === 'fall' ? 0.07 : 0.15;
    } else {
      target = FOOT; tau = 0.45;
      var g = sc.g, ms = sc.markers;
      if (g && g.phase === 'betting' && ms.length && ms[ms.length - 1].height > 9) {
        var topH = ms[ms.length - 1].height, el = this._elapsed(t) / 1000, left = (this._left(t) || 0) / 1000;
        var upTime = clamp(topH * 0.7, 8, 32), cyc = (el - 2.5) / upTime, tri = cyc < 0 ? 0 : 1 - Math.abs((cyc % 2) - 1);
        var back = smooth(2.2, 5, left);
        target = FOOT + ease(tri) * Math.max(0, (topH - 3)) * LV * back;
        tau = 0.35;
      }
    }
    var lo = FOOT, hi = this.H * LV + 400;
    target = clamp(target, lo, hi);
    if (!cam.init) { cam.y = target; cam.init = true; }
    else cam.y += (target - cam.y) * (1 - Math.exp(-dt / tau));
    cam.x = 0;
  };

  P._chunk = function (idx) {
    var key = this.cfg.theme + ':' + idx + ':' + this.k + ':' + this.H, cv = this.cache[key];
    if (cv) return cv;
    cv = bakeWall(this, idx);
    this.cache[key] = cv; this.cacheList.push(key);
    if (this.cacheList.length > 24) delete this.cache[this.cacheList.shift()];
    return cv;
  };

  P._draw = function (sc, t, T) {
    var c = this.ctx, k = this.k, th = this.th, cam = this.cam, H = this.H, pose = sc.pose, i;
    I_TH = th;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, this.canvas.width, this.canvas.height);
    c.setTransform(k, 0, 0, k, 0, 0);
    c.save();
    rr(c, 0, 0, VW, VH, 20); c.clip();
    // a shake for big moments
    var sh = this._shake(sc, T);
    if (sh) c.translate(sh.x, sh.y);
    // the backdrop and the wall
    c.fillStyle = mixc(th.sky0, th.sky1, smooth(0, 1, cam.y / (H * LV))); c.fillRect(-20, -20, VW + 40, VH + 40);
    DRAW.far(this, c, sc, T);
    var lo = Math.floor((cam.y - (VH - ANCHOR_Y) - 30) / CH), hi = Math.floor((cam.y + ANCHOR_Y + 30) / CH);
    for (i = lo; i <= hi; i++) {
      if (i < -2 || i * CH > H * LV + 60) continue;
      var cvk = this._chunk(i), dy0 = ANCHOR_Y - ((i + 1) * CH - cam.y) - 0.5, lysH = ANCHOR_Y - (LAVA_Y - cam.y);
      if (lysH > 30 && lysH < VH + 120 && dy0 < lysH && dy0 + CH + 1 > lysH - 130) {      // heat haze: the rock above the lava shimmers
        var ya = Math.max(dy0, lysH - 130), yb = Math.min(dy0 + CH + 1, lysH + 4), yy, sc2 = cvk.height / (CH + 1);
        if (ya > dy0) c.drawImage(cvk, 0, 0, cvk.width, (ya - dy0) * sc2, 0, dy0, VW, ya - dy0);
        for (yy = ya; yy < yb; yy += 10) {
          var hf = 1 - (lysH - yy) / 130, ha = 2.4 * hf * hf * (hf > 0 ? 1 : 0), hd = Math.sin(yy * 0.15 + T / 230) * ha + Math.sin(yy * 0.06 - T / 400) * ha * 0.6, hh2 = Math.min(10, yb - yy);
          c.drawImage(cvk, 0, (yy - dy0) * sc2, cvk.width, hh2 * sc2, hd - 3, yy, VW + 6, hh2);
        }
        if (yb < dy0 + CH + 1) c.drawImage(cvk, 0, (yb - dy0) * sc2, cvk.width, cvk.height - (yb - dy0) * sc2, 0, yb, VW, dy0 + CH + 1 - yb);
      } else c.drawImage(cvk, 0, dy0, VW, CH + 1);
    }
    DRAW.top(this, c, sc, T);
    DRAW.torches(this, c, sc, T);
    DRAW.lava(this, c, sc, T);
    DRAW.platform(this, c, sc, T);
    DRAW.routes(this, c, sc, T);
    DRAW.ruler(this, c, sc, T);
    DRAW.props(this, c, sc, T);
    DRAW.flags(this, c, sc, T);
    // the soul
    var sp = W2S(this, pose.x, pose.y);
    this.soulScreen = sp;
    if (!pose.gone && !pose.hidden) this._ghost(c, sc, pose, sp, T);
    if (DRAW.finaleBack) DRAW.finaleBack(this, c, sc, T);
    this._drawSoul(c, sc, pose, sp, T);
    DRAW.soulFx(this, c, sc, pose, sp, T);
    if (!pose.gone && !pose.hidden) this._dust(c, sc, pose, sp, T);
    if (DRAW.finaleFront) DRAW.finaleFront(this, c, sc, T);
    // talk
    this._bubbles(c, sc, pose, sp, T);
    DRAW.foreground(this, c, sc, T);
    this._embers(c, sc, T);
    this._light(c, sc, T);
    // a cut between scenes: a quick fade in
    var fa = this._fadeAt != null ? clamp(1 - (now() - this._fadeAt) / 450, 0, 1) : 0;
    if (fa > 0) { c.fillStyle = 'rgba(10,2,6,' + fa + ')'; c.fillRect(-20, -20, VW + 40, VH + 40); }
    c.restore();
    // the frame
    c.lineWidth = 5; c.strokeStyle = th.line; rr(c, 2.5, 2.5, VW - 5, VH - 5, 19); c.stroke();
    c.lineWidth = 2; c.strokeStyle = 'rgba(0,0,0,.6)'; rr(c, 6, 6, VW - 12, VH - 12, 16); c.stroke();
  };

  P._shake = function (sc, T) {
    var tl = sc.tl, p = sc.pose;
    if (!tl || !p || !p.fx) return null;
    var fx = p.fx, a = 0;
    if (fx.type === 'fall') {
      if (fx.style === 'bonk' && fx.bonk && fx.u < fx.imp) { a = Math.max(0, 1 - fx.bonk.f * 5) * 5; if (fx.bonk.k === 0 && fx.bonk.f < 0.2) a = 0; }
      else if (fx.u >= fx.imp && fx.u < fx.imp + 0.06) a = 6 * (1 - (fx.u - fx.imp) / 0.06);
    } else if (fx.type === 'fatal' && fx.cause === 'geyser' && fx.u > 0.46 && fx.u < 0.6) a = 5;
    else if (p.line === 'geyser') a = Math.sin(clamp(p.u || 0, 0, 1) * Math.PI) * 0;
    if (!a) return null;
    return { x: Math.sin(T / 17) * a, y: Math.cos(T / 13) * a };
  };

  P._drawSoul = function (c, sc, pose, sp, T) {
    var g = sc.g, soul = (g && g.soul) || { skin: 0, gear: 0 };
    if (pose.gone || pose.hidden) return;
    var fx = pose.fx;
    var blink = (T % 3400) < 140 ? 1 : 0;
    function sc2(pt) { return pt ? { x: LANE_X + pt.x - 0, y: ANCHOR_Y - (pt.y - this.cam.y) } : null; }
    var self = this;
    function S(pt) { return pt ? { x: LANE_X + pt.x - self.cam.x, y: ANCHOR_Y - (pt.y - self.cam.y) } : null; }
    var o = { soot: pose.soot || 0, x: sp.x, y: sp.y, s: 1, rot: pose.rot || 0, lean: pose.lean || 0, hands: pose.hands ? [S(pose.hands[0]), S(pose.hands[1])] : null,
      feet: pose.feet ? [S(pose.feet[0]), S(pose.feet[1])] : null, grip: pose.grip, look: pose.look, mood: pose.mood, mouth: pose.mouth, sweat: pose.sweat,
      blink: blink, skin: soul.skin, gear: soul.gear, t: T, halo: pose.halo, tailWag: pose.tailWag, headTilt: pose.headTilt,
      glow: self.th.glow, rim: clamp(0.34 + 0.8 * Math.exp(-Math.max(0, sc.level || 0) / 18), 0, 1) };
    c.save();
    if (pose.lava) {                                      // sunk to the neck: only what is above the surface shows
      var ys = ANCHOR_Y - (LAVA_Y - self.cam.y);
      c.beginPath(); c.rect(-40, -200, VW + 80, ys + 200); c.clip();
    }
    if (pose.sit > 0.05) {                                // a wide ledge to sit on
      var ly = ANCHOR_Y - (sc.level * LV - self.cam.y), sw = pose.sit;
      slab(c, sp.x, ly + 1, 120 * sw, 15, self.th, mixc(self.th.rockHi, self.th.strata[1], 0.3));
    }
    drawSoul(c, o);
    c.restore();
  };


  // The ghost of him: a cold glow, a wisp trail behind his chest, soft shadows on the wall, dust trickling off the holds.
  P._ghost = function (c, sc, pose, sp, T) {
    var self = this, tr = this.trail || (this.trail = []), last = tr[tr.length - 1], i;
    if (!last || T - last.t > 28) tr.push({ x: pose.x, y: pose.y + 18, t: T });
    while (tr.length && (T - tr[0].t > 850 || tr.length > 48)) tr.shift();
    var eff = pose.mood === 'strain' || pose.mood === 'scared' || pose.mood === 'ouch' ? 1 : 0.4, th = this.th;
    // shadows: light comes from the lava below, so he throws a soft shadow up the wall
    c.save();
    var sg = c.createRadialGradient(sp.x + 8, sp.y - 34, 4, sp.x + 8, sp.y - 34, 52);
    sg.addColorStop(0, 'rgba(6,2,4,.4)'); sg.addColorStop(1, 'rgba(6,2,4,0)');
    c.save(); c.translate(sp.x + 8, sp.y - 34); c.scale(0.62, 1.35); c.translate(-(sp.x + 8), -(sp.y - 34)); c.fillStyle = sg; c.fillRect(sp.x - 50, sp.y - 90, 120, 120); c.restore();
    c.lineCap = 'round';
    for (i = 0; i < 2; i++) {
      var hnd = i ? pose.hands && pose.hands[1] : pose.hands && pose.hands[0]; if (!hnd) continue;
      var hs = { x: LANE_X + hnd.x - this.cam.x, y: ANCHOR_Y - (hnd.y - this.cam.y) };
      c.strokeStyle = 'rgba(6,2,4,.1)'; c.lineWidth = 12; c.beginPath(); c.moveTo(sp.x + (i ? 24 : -12), sp.y - 46); c.lineTo(hs.x + 6, hs.y - 8); c.stroke();
    }
    c.restore();
    // ghostly glow and wisps (additive)
    c.save(); c.globalCompositeOperation = 'lighter';
    var ag = c.createRadialGradient(sp.x, sp.y - 24, 6, sp.x, sp.y - 24, 70);
    ag.addColorStop(0, 'rgba(150,200,255,' + (0.1 + 0.05 * eff) + ')'); ag.addColorStop(1, 'rgba(150,200,255,0)');
    c.fillStyle = ag; c.fillRect(sp.x - 70, sp.y - 94, 140, 140);
    c.lineCap = 'round';
    for (i = 3; i < tr.length; i += 3) {
      var a0 = tr[i - 3], a1 = tr[i], age = (T - a1.t) / 850, al = Math.pow(1 - age, 2) * 0.22;
      var p0 = W2S(self, a0.x, a0.y), p1 = W2S(self, a1.x, a1.y);
      var seglen = Math.abs(p1.x - p0.x) + Math.abs(p1.y - p0.y);
      if (seglen > 160 || seglen < 2.5 || al < 0.01) continue;
      al *= Math.min(1, seglen / 9);
      var wob = Math.sin(T / 190 + i * 0.7) * 4 * age;
      c.strokeStyle = 'rgba(165,210,255,' + al.toFixed(3) + ')'; c.lineWidth = 2 + 6 * (1 - age);
      c.beginPath(); c.moveTo(p0.x + wob, p0.y); c.lineTo(p1.x + wob, p1.y); c.stroke();
    }
    for (i = 0; i < 2; i++) {                                         // wisps curling up off his shoulders
      var ph = ((T / 2600) + i * 0.33) % 1, wx = sp.x + (i - 1) * 16 + Math.sin(T / 500 + i * 2) * 5, wy = sp.y - 52 - ph * 70;
      c.strokeStyle = 'rgba(175,215,255,' + (0.2 * (1 - ph) * (1 - ph)).toFixed(3) + ')'; c.lineWidth = 2.4 * (1 - ph) + 0.6;
      c.beginPath(); c.moveTo(wx, sp.y - 52 - ph * 40); c.bezierCurveTo(wx + 9 * Math.sin(T / 330 + i), wy + 20, wx - 9 * Math.sin(T / 410 + i), wy + 8, wx + 4, wy); c.stroke();
    }
    c.restore();
  };

  // dust shaken loose from the holds, more when he strains
  P._dust = function (c, sc, pose, sp, T) {
    var anchors = [], i, j, eff = pose.mood === 'strain' || pose.mood === 'scared' ? 1 : 0.35;
    if (!sc.tl || pose.fx && pose.fx.type === 'fall') return;
    if (pose.hands) for (i = 0; i < 2; i++) if (pose.hands[i]) anchors.push(pose.hands[i]);
    if (pose.feet) for (i = 0; i < 2; i++) if (pose.feet[i]) anchors.push(pose.feet[i]);
    for (i = 0; i < anchors.length; i++) {
      var a = W2S(this, anchors[i].x, anchors[i].y);
      for (j = 0; j < 3; j++) {
        var h0 = hash(i * 17 + j * 5 + 3), ph = ((T / 1100) + h0) % 1, al = (1 - ph) * 0.34 * eff;
        if (al < 0.02) continue;
        c.fillStyle = 'rgba(176,156,134,' + al.toFixed(3) + ')';
        c.beginPath(); c.arc(a.x + (h0 - 0.5) * 12 + Math.sin(T / 300 + i + j) * 3, a.y + 6 + ph * ph * 46, 0.9 + ph * 1.8, 0, TAU); c.fill();
      }
    }
  };

  P._bubbles = function (c, sc, pose, sp, T) {
    var th = this.th, tl = sc.tl, i;
    if (tl) {
      var bs = tl.bubbleAt(sc.tms);
      for (i = 0; i < bs.length; i++) {
        var q = bs[i], age = sc.tms - q.t0, life = q.t1 - q.t0;
        if (q.who === 'demon') {
          var dx = LANE_X + (q.side > 0 ? 196 : -178), dy = ANCHOR_Y - (q.lv * LV - this.cam.y) - LV * 0.9 - 110;
          if (q.fin) { dx = LANE_X + DEMON.x; dy = ANCHOR_Y - (80 - this.cam.y); }
          bubble(c, th, q.text, dx, dy, age, life, 'demon');
        } else {
          var um = pose && pose.fx && pose.fx.umb, lift = um && um.burn < 0.9 ? 80 * um.open : 0;       // above the umbrella, not on it
          bubble(c, th, q.text, sp.x + 8, sp.y - 112 - lift, age, life, 'soul');
        }
      }
    } else if (sc.g || this.st) {
      // between climbs he mutters now and then
      var n = Math.floor(T / 9000), ph = T % 9000, r = hash2(n, 99);
      if (ph > 2600 && ph < 4800 && (!sc.g || sc.ph === 'betting')) {
        var nm = (sc.g && sc.g.soul && sc.g.soul.name) || (this.st && this.st.idle && this.st.idle.soul && this.st.idle.soul.name) || 'Gary';
        bubble(c, th, fillName(pick(LINES.ready, r), nm), sp.x + 8, sp.y - 112, ph - 2600, 2200, 'soul');
      }
    }
  };

  P._embers = function (c, sc, T) {
    var th = this.th, cam = this.cam, alt = clamp(cam.y / (this.H * LV), 0, 1), n = Math.round(46 - 24 * alt);
    for (var i = 0; i < n; i++) {
      var r1 = hash(i * 7 + 1), r2 = hash(i * 7 + 2), r3 = hash(i * 7 + 3), r4 = hash(i * 7 + 4), spd = 24 + r2 * 52, par = 0.5 + r3 * 0.6;
      var px = r1 * VW + Math.sin(T / 900 + i * 1.7) * 18 + (T / 1000) * 3 * (r3 - 0.5);
      px = ((px % VW) + VW) % VW;
      var span = VH * 1.4, py = ((r4 * span - T * spd / 1000 + cam.y * par) % span + span) % span - VH * 0.2;
      var al = (0.35 + 0.65 * Math.abs(Math.sin(T / 300 + i))) * (0.35 + 0.65 * (1 - alt * 0.5)) * smooth(0, 60, py + 30) * smooth(0, 60, VH - py);
      var rad = 1.2 + r3 * 2.6;
      c.fillStyle = 'rgba(' + th.glow + ',' + clamp(al, 0, 1) * 0.35 + ')'; c.beginPath(); c.arc(px, py, rad * 3, 0, TAU); c.fill();
      c.fillStyle = r3 > 0.5 ? th.lava1 : th.lava2; c.globalAlpha = clamp(al, 0, 1); c.beginPath(); c.arc(px, py, rad, 0, TAU); c.fill(); c.globalAlpha = 1;
    }
    // ash drifting down: near flakes are big and soft (out of focus), far ones small and sharp
    for (i = 0; i < 26; i++) {
      var q1 = hash(i * 11 + 101), q2 = hash(i * 11 + 102), q3 = hash(i * 11 + 103), q4 = hash(i * 11 + 104), near = q3 > 0.7;
      var sp2 = 14 + q2 * 22, span2 = VH * 1.3, par2 = near ? 1.15 : 0.55 + q3 * 0.3;
      var ax = ((q1 * VW + Math.sin(T / 1300 + i * 2.1) * 26 + T * 0.004 * (q4 - 0.3)) % VW + VW) % VW;
      var ay = (((q4 * span2 + T * sp2 / 1000 - cam.y * par2 * 0.5) % span2) + span2) % span2 - VH * 0.15;
      var ar = near ? 3.2 + q2 * 2.4 : 1 + q2 * 1.4, aa = (near ? 0.16 : 0.3) * smooth(0, 50, ay + 20) * smooth(0, 50, VH - ay);
      if (aa < 0.01) continue;
      c.save(); c.translate(ax, ay); c.rotate(T / 700 * (q1 - 0.5) + i);
      c.fillStyle = 'rgba(' + (near ? '120,108,104' : '170,160,154') + ',' + aa.toFixed(3) + ')';
      c.beginPath(); c.ellipse(0, 0, ar, ar * 0.55, 0, 0, TAU); c.fill(); c.restore();
    }
  };

  P._light = function (c, sc, T) {
    var th = this.th, cam = this.cam, alt = clamp(cam.y / (this.H * LV), 0, 1);
    // the lava glow from below, strongest near the foot of the pit
    var low = 1 - smooth(0, 11 * LV, cam.y - FOOT);
    if (low > 0) {
      var gg = c.createLinearGradient(0, VH, 0, VH * 0.35);
      gg.addColorStop(0, 'rgba(' + th.glow + ',' + 0.5 * low + ')'); gg.addColorStop(1, 'rgba(' + th.glow + ',0)');
      c.fillStyle = gg; c.fillRect(0, VH * 0.35, VW, VH * 0.65);
    }
    // higher up it turns cool and dim, a hint of dawn near the top
    var dim = 1 - smooth(0.9, 1, alt);
    c.fillStyle = 'rgba(0,0,0,' + (0.06 + alt * 0.05) * dim + ')'; c.fillRect(0, 0, VW, VH);
    var vg = c.createRadialGradient(VW / 2, VH * 0.55, VH * 0.3, VW / 2, VH * 0.55, VH * 0.82);
    vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(0,0,0,' + (0.28 + 0.24 * dim) + ')');
    c.fillStyle = vg; c.fillRect(0, 0, VW, VH);
  };

  // ------------------------------------------------------------------ the HUD (DOM)
  P._set = function (key, el, html) {
    if (this.keys[key] === html) return;
    this.keys[key] = html;
    el.innerHTML = html;
  };
  P._show = function (el, on) { el.style.display = on ? '' : 'none'; };

  var CAUSE_TEXT = { rock: 'The ledge crumbled under him', hand: 'A skeleton hand yanked him down', rope: 'The rope snapped', chain: 'A chain link gave way',
    rib: 'A rib snapped', bat: 'The bats won this round', geyser: 'A lava geyser blasted him off', demon: 'A demon pried his fingers loose', tired: 'His arms just gave out' };
  var STYLE_TEXT = { bonk: 'bonking off every ledge on the way down', yelp: 'with a very long "oh no" and a splash', cauldron: 'straight into a cauldron. Soup\'s on',
    flick: 'into a hex-demon\'s fingers. FLICK!', grinder: 'into the sausage grinder', boing: 'bouncing on a bat-wing trampoline', umbrella: 'with a tiny umbrella that caught fire' };

  P._dom = function (sc, t) {
    var g = sc.g, st = this.st, cfg = this.cfg, idle = (st && st.idle) || {}, ph = sc.ph, tl = sc.tl;
    var cur = (g && g.currency) || idle.currency || cfg.currency || 'coins', H = this.H, left = this._left(t);
    var name = g ? g.soul.name : ((idle.soul && idle.soul.name) || 'Gary');
    // title block
    var played = g && g.summary ? (g.summary.played || 0) : -1;       // a game over after fewer climbs than planned (a quiet last window)
    this._set('sub', this.el.sub, esc(!g ? 'Next up: ' + name : ph === 'over' && played >= 0 && played < g.climb ?
      (played ? played + ' of ' + g.climbs + ' climbs played' : 'No climb played') : 'Climb ' + g.climb + ' of ' + g.climbs + ' · ' + name));
    // the timer
    var tl_ = '', tv = '', last = false;
    if (ph === 'betting') { tl_ = 'Bets close in'; tv = Math.ceil(left / 1000) + 's'; last = left <= 10000; }
    else if (ph === 'climbing') { tl_ = 'Climbing'; tv = '…'; }
    else if (ph === 'result') { tl_ = g.last && g.last.escaped ? 'Escaped!' : 'Fell'; tv = ''; }
    else if (ph === 'over') { tl_ = 'Game over'; tv = ''; }
    else { tl_ = 'Waiting'; tv = ''; }
    this.el.tlabel.textContent = tl_; this.el.tval.textContent = tv;
    this.el.timer.className = 'hcl-box hcl-timer' + (last ? ' last' : '');
    if (last && ph === 'betting') {
      var sec = Math.ceil(left / 1000);
      if (sec <= 5 && sec >= 1 && this._beep !== g.id + '/' + g.climb + '/' + sec) { this._beep = g.id + '/' + g.climb + '/' + sec; this._snd('beep'); }
    }
    // the height meter
    var lvl = tl ? sc.level : 0, best = tl ? tl.reached(sc.tms) : 0;
    this.el.fill.style.height = (clamp(lvl / H, 0, 1) * 100).toFixed(2) + '%';
    this.el.dot.style.bottom = (clamp(lvl / H, 0, 1) * 100).toFixed(2) + '%';
    var hv = String(Math.floor(lvl + 0.02));
    if (this.keys.hv !== hv) { this.keys.hv = hv; this.el.hv.textContent = hv; }
    var bv = String(best);
    if (this.keys.bv !== bv) { this.keys.bv = bv; this.el.bv.textContent = bv; }
    var mults = (g && g.mults) || idle.mults || null, worth = mults && best > 0 && mults[best - 1] ? mults[best - 1] : 0;   // what a bet on his best height pays
    this._set('worth', this.el.worth, worth ? ' · pays <b>' + mult(worth) + '</b>' : '');
    this._set('hmax', this.el.hmax, '/ ' + H);
    var ms = sc.markers || [], ticks = '', rows = [], i;
    for (i = 0; i < ms.length; i++) {
      var stt = tl ? tl.flag(ms[i].height, sc.tms) : 'open';
      ticks += '<div class="hcl-tick ' + stt + '" style="bottom:' + (clamp(ms[i].height / H, 0, 1) * 100).toFixed(2) + '%"><i></i></div>';
      rows.push({ m: ms[i], st: stt });
    }
    this._set('ticks', this.el.ticks, ticks);
    this._show(this.el.gtop, true);
    var show = rows.filter(function (r) { return r.st === 'open'; }).slice(0, 3), head = 'Next flags';
    if (!show.length) { show = rows.slice(-3); head = rows.length ? 'Flags' : 'Next flags'; }
    if (ph === 'betting' || !g) { show = rows.slice(0, 3); head = rows.length ? 'Flags on the wall' : 'No flags yet'; }
    this._set('nl', this.el.nl, head);
    var nh = show.map(function (r) {
      var us = r.m.users, nm = us.length === 1 ? us[0].user : us[0].user + ' +' + (us.length - 1);
      return '<div class="hcl-nf ' + r.st + '"><div><b>LV ' + r.m.height + '</b><u>' + esc(nm) + '</u></div><i>' + mult(r.m.mult) + '</i></div>';
    }).join('');
    if (!nh) nh = '<div class="hcl-empty">' + (g && ph !== 'betting' ? 'No flags on this climb' : 'Bets make flags') + '</div>';
    this._set('next', this.el.next, nh);
    // players / results
    var showP = !!cfg.show_players && !!g;
    this._show(this.el.players, !!cfg.show_players);
    var phTitle = ph === 'result' || ph === 'over' ? 'Results' : 'On the line', html = '', moreN = 0;
    if (cfg.show_players) {
      var max = +cfg.players_max || 8;
      if ((ph === 'result' || ph === 'over') && g && g.last) {
        var all = {}, order = [];
        function row(u) { var e = all[u] || (all[u] = { user: u, net: 0, bets: [] }); if (order.indexOf(e) < 0) order.push(e); return e; }
        (g.last.winners || []).forEach(function (w) { var e = row(w.user); e.net += w.pays - w.amount; e.bets.push('L' + w.height + ' ✓'); });
        (g.last.losers || []).forEach(function (w) { var e = row(w.user); e.net -= w.amount; e.bets.push('L' + w.height + ' ✗'); });
        order.sort(function (a, b) { return b.net - a.net; });
        for (i = 0; i < Math.min(order.length, max); i++) {
          var e = order[i], net = e.net;
          html += '<div class="hcl-p"><u>' + esc(e.user) + '<small>' + esc(e.bets.slice(0, 3).join(' ')) + '</small></u><i class="' + (net > 0 ? 'ok' : net < 0 ? 'bad' : 'dim') + '">' + (net > 0 ? '+' : net === 0 ? '±' : '') + fmt(net) + '</i></div>';
        }
        moreN = Math.max(0, order.length - max);
        if (!order.length) html = '<div class="hcl-empty">Nobody bet on this climb</div>';
      } else if (g) {
        var ps = g.players || [];
        if (!ps.length) html = '<div class="hcl-empty">' + (ph === 'betting' ? 'No bets yet — pick a height!' : '—') + '</div>';
        for (i = 0; i < Math.min(ps.length, max); i++) {
          var p = ps[i], hs = p.bets.map(function (b) {
            var s2 = tl ? tl.flag(b.height, sc.tms) : 'open';
            return '<span class="' + (s2 === 'passed' ? 'ok' : s2 === 'lost' ? 'dim' : '') + '">L' + b.height + '</span>';
          }).join(' ');
          html += '<div class="hcl-p"><u>' + esc(p.user) + '<small>' + hs + '</small></u><i>' + fmt(p.stake) + '<small>' + esc(cur) + '</small></i></div>';
        }
        moreN = Math.max(0, ps.length - max);
      } else html = '<div class="hcl-empty">—</div>';
      this._set('players', this.el.pb, html);
    }
    this._set('ph', this.el.ph, phTitle + (moreN ? '<span class="hcl-more">+' + moreN + ' more</span>' : ''));
    // the rules box (bets open) and the ladder
    var odds = g ? g.odds : (idle.odds || []);
    var showR = !!cfg.show_rules && (ph === 'betting' || (!g && st && st.visible));
    this._show(this.el.rules, showR);
    if (showR) {
      var warn = g && ph === 'betting' && left <= 10000 ? '<div class="warn">⏱ Bets close in ' + Math.ceil(left / 1000) + '!</div>' : '';
      var rh = '<h4>' + (g ? (g.climb === 1 ? 'Bets open' : 'Climb ' + g.climb) : 'Next climb soon') + '</h4>' +
        '<p>How high will <em>' + esc(name) + '</em> get? Bet a <em>height</em> (1–' + H + '): reach it and you win your bet × its multiplier, fall first and it is gone. The top is the <em>escape</em>.</p>';
      if (cfg.show_odds && odds.length) {
        rh += '<table><tr><th>Height</th><th>Chance</th><th>Pays</th></tr>';
        for (i = 0; i < odds.length; i++) rh += '<tr><td>' + odds[i].height + (odds[i].height >= H ? ' ⛰' : '') + '</td><td>' + (+odds[i].chance_pct).toFixed(odds[i].chance_pct < 10 ? 1 : 0) + '%</td><td>' + mult(odds[i].mult) + '</td></tr>';
        rh += '</table>';
      }
      var cmd = cfg.commands_text || (g && g.commands_text);
      rh += (cmd ? '<div class="cmd">' + esc(cmd) + '</div>' : '') + '<div class="hcl-w"></div>';
      this._set('rules', this.el.rules, rh);
      var w = this.el.rules.querySelector('.hcl-w');
      if (w && w.innerHTML !== warn) w.innerHTML = warn;
    }
    // the result card and the game-over card
    var ban = '';
    if (ph === 'result' && g && g.last) ban = this._resultCard(g, cur, name);
    else if (ph === 'over' && g && g.summary) ban = this._summaryCard(g, cur);
    this._set('banner', this.el.banner, ban);
    this._show(this.el.banner, !!ban);
    this._set('test', this.el.test, g && g.test ? 'TEST GAME · NO COINS' : '');
  };

  P._resultCard = function (g, cur, name) {
    var l = g.last, esc_ = l.escaped, soul = l.soul || name;
    var kick = esc_ ? 'Jackpot' : 'Climb ' + l.climb + ' of ' + g.climbs;
    var big = esc_ ? esc(soul) + ' ESCAPED THE PIT!' : esc(soul) + ' reached level ' + l.max;
    var why = esc_ ? 'All ' + g.height + ' levels. Fresh air, grass, and a very long nap.' :
      esc(CAUSE_TEXT[l.cause] || 'He fell') + ', ' + esc(STYLE_TEXT[l.style] || 'all the way down') + '.';
    var rows = (l.winners || []).slice(0, 5).map(function (w) {
      var net = w.pays - w.amount;                       // what he won on top of his stake (a x1.00 bet just gets it back)
      return '<div class="row"><span>' + esc(w.user) + '<small>level ' + w.height + ' · ' + mult(w.mult) + (net ? '' : ' · stake back') + '</small></span><i class="' + (net > 0 ? 'pos' : '') + '">' + (net > 0 ? '+' + fmt(net) : '±0') + '</i></div>';
    }).join('');
    var more = (l.winners || []).length > 5 ? '<div class="none">+' + ((l.winners || []).length - 5) + ' more winners</div>' : '';
    var lost = (l.losers || []).slice(0, 3).map(function (w) {
      return '<div class="row"><span>' + esc(w.user) + '<small>level ' + w.height + '</small></span><i class="neg">−' + fmt(w.amount) + '</i></div>';
    }).join('');
    var lmore = (l.losers || []).length > 3 ? '<div class="none">+' + ((l.losers || []).length - 3) + ' more lost</div>' : '';
    var body = rows ? rows + more : '<div class="none">' + ((l.losers || []).length ? 'Nobody guessed high enough.' : 'Nobody bet.') + '</div>';
    return '<div class="card"><div class="kick">' + kick + '</div><div class="big ' + (esc_ ? 'win' : '') + '">' + big + '</div><div class="why">' + why + '</div>' +
      '<div class="rows">' + body + lost + lmore + '</div></div>';
  };

  P._summaryCard = function (g, cur) {
    var s = g.summary, res = s.results || [];
    var title = s.outcome === 'no_bets' ? 'No bets — no climb' : s.outcome === 'complete' ? (res.some(function (r) { return r.escaped; }) ? 'Somebody got out!' : 'Game over') : esc(s.text);
    var sub = s.outcome === 'no_bets' ? 'Place your bets when the timer starts' :
      res.length ? res.map(function (r) { return esc(r.soul) + ': ' + (r.escaped ? 'ESCAPED' : r.max); }).join(' · ') : '';
    var rows = (s.players || []).slice(0, 6).map(function (p) {
      return '<div class="row"><span>' + esc(p.user) + '</span><i class="' + (p.net > 0 ? 'pos' : p.net < 0 ? 'neg' : '') + '">' + (p.net > 0 ? '+' : '') + fmt(p.net) + '</i></div>';
    }).join('');
    if (!rows && s.outcome !== 'no_bets') rows = '<div class="none">Nobody bet</div>';
    return '<div class="card"><div class="kick">Final</div><div class="big">' + title + '</div><div class="why">' + sub + '</div><div class="rows">' + rows + '</div>' +
      (s.outcome !== 'no_bets' ? '<div class="tot"><span>Bet ' + fmt(s.total_bet) + '</span><span>Paid ' + fmt(s.total_paid) + '</span><span>House ' + (s.house_net >= 0 ? '+' : '') + fmt(s.house_net) + '</span></div>' : '') + '</div>';
  };

  // ------------------------------------------------------------------ sound cues, from the script
  P._audio = function (sc, t) {
    var tl = sc.tl;
    if (!tl || !this.sfx || !this.cfg.sfx || sc.ph !== 'climbing') {
      if (tl && sc.ph !== 'climbing') this._endSounds(sc);
      return;
    }
    var tms = sc.tms, i = tl.index(tms), b = tl.beats[i], key = i + ':';
    if (!b) return;
    var c = this;
    function cue(n, frac, snd) { c._cue(key + n, b.t + b.d * frac, tms, snd || n); }
    switch (b.ev) {
      case 'ready': cue('gulp', 0.62); break;
      case 'climb': cue('step', 0.5, 'step'); if (hash(b.s) < 0.1) cue('grunt', 0.3); break;
      case 'cheer': cue('ding', 0.02); break;
      case 'idle': if (b.kind === 'gulp') cue('gulp', 0.3); else if (b.kind === 'pant') cue('sigh', 0.1); else if (b.kind === 'wave') cue('boing', 0.3); break;
      case 'rest': cue('sigh', 0.15); break;
      case 'taunt': cue('taunt', 0.08); cue('reply', 0.66); break;
      case 'slip': cue('slip', 0.0); cue('catch', 0.52); break;
      case 'bat': cue('bats', 0.05); break;
      case 'geyser': cue('rumble', 0.0); cue('geyser', 0.24); break;
      case 'deadend': cue('ouch', 0.46); break;
      case 'grab': cue('clatter', 0.1); cue('thud', 0.8); break;
      case 'fray': cue('creak', 0.0); cue('snap', 0.3); break;
      case 'fatal': {
        var rel = FATAL_RELEASE[b.cause] || 0.6;
        if (b.cause === 'rock') { cue('creak', 0.1); cue('pop', rel); }
        else if (b.cause === 'hand') cue('clatter', 0.12);
        else if (b.cause === 'rope' || b.cause === 'chain' || b.cause === 'rib') { cue('creak', 0.1); cue('snap', rel); }
        else if (b.cause === 'bat') cue('bats', 0.05);
        else if (b.cause === 'geyser') { cue('rumble', 0.0); cue('geyser', rel - 0.05); }
        else if (b.cause === 'demon') { cue('taunt', 0.15); cue('pop', 0.45); cue('pop', 0.67); }
        else cue('sigh', 0.3);
        break;
      }
      case 'fall': {
        var imp = FALL_IMPACT[b.style] || 0.55;
        cue('whistle', 0.02);
        if (b.style === 'bonk') { var n = b.n || 1; for (var q = 0; q < n; q++) cue('bonk' + q, imp * (q + 1) / n - 0.01, 'bonk'); cue('sad', imp + 0.04); }
        else if (b.style === 'yelp') { cue('splash', imp); cue('sizzle', imp + 0.05); }
        else if (b.style === 'cauldron') { cue('splash', imp); cue('sad', imp + 0.1); }
        else if (b.style === 'flick') { cue('thud', imp, 'catch'); cue('flick', imp + (1 - imp) * 0.24 * 1.0); }
        else if (b.style === 'grinder') { cue('thud', imp, 'catch'); cue('grind', imp + 0.04); cue('pop', imp + (1 - imp) * 0.75); }
        else if (b.style === 'boing') { cue('boing', imp); cue('boing2', imp + (1 - imp) * 0.34, 'boing'); cue('boing3', imp + (1 - imp) * 0.62, 'boing'); cue('sad', imp + (1 - imp) * 0.9); }
        else { cue('poof', 0.5); cue('sizzle', 0.52); cue('thud', imp, 'catch'); }
        break;
      }
      case 'escape': cue('fanfare', 0.55); break;
      default: break;
    }
  };
  P._endSounds = function (sc) {
    if (sc.ph === 'result' && sc.g && sc.g.last && !this.done['end:' + sc.g.id + sc.g.climb]) {
      this.done['end:' + sc.g.id + sc.g.climb] = 1;
    }
  };

  // ==================================================================== the finale props
  // The pit has a convenient surprise for every way of falling: when the end is near the prop is wheeled in (or
  // climbs out of the lava) from the right, ready for him. Drawn behind (back) and in front of (front) the soul.
  function finaleInfo(sc) {
    var tl = sc.tl, fb = tl && tl.fallBeat;
    if (!fb) return null;
    var fa = tl.fatalBeat, t0 = fa ? fa.t + fa.d * 0.4 : fb.t - 400;
    return { b: fb, style: fb.style, arr: ease((sc.tms - t0) / 1000), post: sat01((sc.tms - fb.t) / fb.d) };
  }

  function cauldronBack(I, c, th, x, y, arr, T, sc) {
    // (x, y) = rim centre on screen
    var w = 150, bx = x + (1 - arr) * 430;
    c.save(); c.translate(bx, y);
    // the legs and the body
    c.fillStyle = '#18141a'; c.strokeStyle = 'rgba(8,2,6,.95)'; c.lineWidth = 3;
    [-52, 0, 52].forEach(function (lx) { c.beginPath(); c.moveTo(lx - 8, 40); c.lineTo(lx + 8, 40); c.lineTo(lx + 5, 74); c.lineTo(lx - 5, 74); c.closePath(); c.fill(); c.stroke(); });
    // stew behind
    c.fillStyle = '#0a0608'; c.beginPath(); c.ellipse(0, 0, w / 2, 17, 0, 0, TAU); c.fill();
    var sg = c.createRadialGradient(0, 2, 4, 0, 2, w / 2); sg.addColorStop(0, '#9be05a'); sg.addColorStop(0.7, '#5aa13a'); sg.addColorStop(1, '#2d5a22');
    c.fillStyle = sg; c.beginPath(); c.ellipse(0, 3, w / 2 - 7, 12, 0, 0, TAU); c.fill();
    // bubbles + bits
    for (var i = 0; i < 6; i++) {
      var ph = ((T / (700 + i * 90)) + hash(i * 3)) % 1, bx2 = (hash(i * 5 + 1) - 0.5) * (w - 40), by2 = 3 + (hash(i) - 0.5) * 12;
      c.strokeStyle = 'rgba(230,255,190,' + (0.9 - ph * 0.7) + ')'; c.fillStyle = 'rgba(190,240,120,.45)'; c.lineWidth = 1.6;
      c.beginPath(); c.arc(bx2, by2 - ph * 4, 2 + ph * 5, 0, TAU); c.fill(); c.stroke();
    }
    c.fillStyle = '#ff9a2a'; c.beginPath(); c.ellipse(-40 + Math.sin(T / 900) * 6, 4, 9, 3.4, 0.3, 0, TAU); c.fill();     // a carrot
    c.strokeStyle = '#f4e9b8'; c.lineWidth = 3; c.beginPath(); c.ellipse(34 + Math.cos(T / 800) * 5, 5, 8, 3.5, 0, 0, TAU); c.stroke();   // an onion ring
    c.restore();
  }
  function cauldronFront(I, c, th, x, y, arr, T, steam) {
    var w = 150, bx = x + (1 - arr) * 430;
    c.save(); c.translate(bx, y);
    // body (front)
    var g = c.createLinearGradient(-w / 2, 0, w / 2, 0);
    g.addColorStop(0, '#3a3440'); g.addColorStop(0.35, '#1c171f'); g.addColorStop(1, '#0c0a0e');
    c.fillStyle = g; c.strokeStyle = 'rgba(8,2,6,.95)'; c.lineWidth = 3.4;
    c.beginPath(); c.moveTo(-w / 2, 2); c.quadraticCurveTo(-w / 2 - 14, 52, -w / 2 + 22, 66); c.quadraticCurveTo(0, 82, w / 2 - 22, 66); c.quadraticCurveTo(w / 2 + 14, 52, w / 2, 2);
    c.quadraticCurveTo(0, 36, -w / 2, 2); c.closePath(); c.fill(); c.stroke();
    // the hex badge + rim light
    c.beginPath(); for (var i = 0; i < 6; i++) { var a = i / 6 * TAU + TAU / 12; c.lineTo(Math.cos(a) * 15, 40 + Math.sin(a) * 15); } c.closePath();
    c.fillStyle = '#ffb02e'; c.fill(); c.strokeStyle = '#3a1a06'; c.lineWidth = 2.4; c.stroke();
    c.fillStyle = '#3a1a06'; c.font = '900 14px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('S', 0, 41);
    c.strokeStyle = 'rgba(255,255,255,.18)'; c.lineWidth = 3; c.beginPath(); c.moveTo(-w / 2 + 6, 14); c.quadraticCurveTo(-w / 2 - 4, 44, -w / 2 + 24, 60); c.stroke();
    // the front lip of the rim
    c.strokeStyle = 'rgba(8,2,6,.95)'; c.lineWidth = 10; c.beginPath(); c.ellipse(0, 0, w / 2, 17, 0, 0.02 * Math.PI, 0.98 * Math.PI); c.stroke();
    c.strokeStyle = '#4a4252'; c.lineWidth = 6; c.beginPath(); c.ellipse(0, 0, w / 2, 17, 0, 0.02 * Math.PI, 0.98 * Math.PI); c.stroke();
    c.strokeStyle = 'rgba(255,255,255,.25)'; c.lineWidth = 2; c.beginPath(); c.ellipse(0, -1.5, w / 2 - 3, 15, 0, 0.12 * Math.PI, 0.5 * Math.PI); c.stroke();
    // ear handles
    [-1, 1].forEach(function (sd) { c.strokeStyle = 'rgba(8,2,6,.95)'; c.lineWidth = 9; c.beginPath(); c.arc(sd * (w / 2 + 6), 10, 11, sd < 0 ? Math.PI * 0.5 : -Math.PI * 0.5, sd < 0 ? Math.PI * 1.5 : Math.PI * 0.5); c.stroke(); c.strokeStyle = '#4a4252'; c.lineWidth = 4.4; c.beginPath(); c.arc(sd * (w / 2 + 6), 10, 11, sd < 0 ? Math.PI * 0.5 : -Math.PI * 0.5, sd < 0 ? Math.PI * 1.5 : Math.PI * 0.5); c.stroke(); });
    // steam
    for (var j = 0; j < 5; j++) {
      var ph = ((T / 1400) + j * 0.21) % 1, sx = (j - 2) * 24 + Math.sin(T / 500 + j) * 6;
      c.fillStyle = 'rgba(235,245,225,' + 0.4 * (1 - ph) * steam + ')'; c.beginPath(); c.arc(sx, -10 - ph * 70, 8 + ph * 14, 0, TAU); c.fill();
    }
    c.restore();
  }

  function grinderDraw(I, c, th, x, y, arr, T, g) {
    // (x, y) = middle of the machine's top on screen; g = {post}
    var bx = x + (1 - arr) * 440, post = g.post, shake = post > 0.18 && post < 0.55 ? Math.sin(T / 22) * 2.4 : 0;
    c.save(); c.translate(bx + shake, y + (shake ? Math.cos(T / 17) * 1.4 : 0));
    var out = '#0b0710', iron = '#4e4a58', ironD = '#2a2631';
    // wheels
    [-48, 62].forEach(function (wx) { c.fillStyle = '#17131b'; c.strokeStyle = out; c.lineWidth = 3; c.beginPath(); c.arc(wx, 86, 15, 0, TAU); c.fill(); c.stroke(); c.fillStyle = '#7a7486'; c.beginPath(); c.arc(wx, 86, 5, 0, TAU); c.fill(); });
    // the body
    var bg = c.createLinearGradient(-70, 0, 70, 0); bg.addColorStop(0, '#7c7688'); bg.addColorStop(0.3, iron); bg.addColorStop(1, ironD);
    c.fillStyle = bg; c.strokeStyle = out; c.lineWidth = 3.4; c.beginPath(); rr(c, -70, 8, 140, 78, 8); c.fill(); c.stroke();
    // the hopper
    c.fillStyle = '#6b6678'; c.beginPath(); c.moveTo(-62, 8); c.lineTo(-30, -52); c.lineTo(30, -52); c.lineTo(62, 8); c.closePath(); c.fill(); c.stroke();
    c.fillStyle = '#0a0508'; c.beginPath(); c.ellipse(0, -52, 30, 7, 0, 0, TAU); c.fill(); c.stroke();
    c.strokeStyle = 'rgba(255,255,255,.22)'; c.lineWidth = 3; c.beginPath(); c.moveTo(-54, 2); c.lineTo(-27, -48); c.stroke();
    // the label
    c.fillStyle = '#ffd24a'; c.strokeStyle = out; c.lineWidth = 2; c.beginPath(); rr(c, -56, 22, 112, 24, 5); c.fill(); c.stroke();
    c.fillStyle = '#3a1a06'; c.font = '900 11.5px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('GRIND-O-MATIC', 0, 35);
    // the spout to the left
    c.fillStyle = iron; c.strokeStyle = out; c.lineWidth = 3; c.beginPath(); rr(c, -134, 44, 70, 22, 6); c.fill(); c.stroke();
    c.fillStyle = '#0a0508'; c.beginPath(); c.ellipse(-134, 55, 5, 10, 0, 0, TAU); c.fill();
    // a tray
    c.fillStyle = '#c9ced6'; c.strokeStyle = out; c.lineWidth = 2.6; c.beginPath(); rr(c, -168, 74, 118, 10, 4); c.fill(); c.stroke();
    // the pit-wheel: a toothed iron wheel on the right, turning
    var rot = post < 0.18 ? T / 900 : post < 0.5 ? T / 55 : post < 0.58 ? Math.sin(T / 30) * 0.3 : (post < 0.9 ? T / 160 : T / 700);
    c.save(); c.translate(96, 40); c.rotate(rot);
    c.fillStyle = '#34303d'; c.strokeStyle = out; c.lineWidth = 3;
    c.beginPath(); for (var i = 0; i < 16; i++) { var a = i / 16 * TAU, r = i % 2 ? 42 : 52; c.lineTo(Math.cos(a - 0.12) * r, Math.sin(a - 0.12) * r); c.lineTo(Math.cos(a + 0.12) * r, Math.sin(a + 0.12) * r); } c.closePath(); c.fill(); c.stroke();
    c.strokeStyle = '#8a8496'; c.lineWidth = 5; for (var s = 0; s < 3; s++) { c.beginPath(); c.moveTo(0, 0); c.lineTo(Math.cos(s * 1.047) * 38, Math.sin(s * 1.047) * 38); c.moveTo(0, 0); c.lineTo(-Math.cos(s * 1.047) * 38, -Math.sin(s * 1.047) * 38); c.stroke(); }
    c.fillStyle = '#ffb02e'; c.strokeStyle = out; c.lineWidth = 2.4; c.beginPath(); for (var h = 0; h < 6; h++) { var ha = h / 6 * TAU; c.lineTo(Math.cos(ha) * 13, Math.sin(ha) * 13); } c.closePath(); c.fill(); c.stroke();
    c.restore();
    // sparks while it chews, steam
    if (post > 0.2 && post < 0.6) burst(c, 0, -40, (post - 0.2) * g.d, { n: 14, seed: 5, speed: 200, g: 500, life: 700, size: 2.6, colors: ['#ffd23f', '#fff6a0', '#ff8a2a'], spread: 3.4 });
    if (post > 0.18 && post < 0.6) textPop(c, th, 'GRIND!', 0, -88, (post - 0.18) * g.d, 700, '#ffd23f', 26, -0.1);
    // the sausages come out of the spout
    if (post > 0.58) {
      var ex = clamp((post - 0.58) / 0.3, 0, 1), len = ex * 150;
      for (var q = 0; q < 3; q++) {
        var lx = -138 - q * 40 * ex - 20 * ex, ly = 76;
        if (q * 40 > len) continue;
        c.fillStyle = '#d9825c'; c.strokeStyle = '#5a2412'; c.lineWidth = 2.6; c.beginPath(); c.ellipse(lx, ly - 12, 22, 12, 0, 0, TAU); c.fill(); c.stroke();
        c.strokeStyle = 'rgba(255,230,200,.5)'; c.lineWidth = 2; c.beginPath(); c.moveTo(lx - 12, ly - 17); c.lineTo(lx + 8, ly - 18); c.stroke();
      }
    }
    c.restore();
  }

  function drawSausage(c, th, x, y, skin, T, o) {
    // the soul, ground into a sausage link: a face on a plump casing, a little halo, two feet sticking out
    c.save(); c.translate(x, y); c.rotate(o.rot || 0);
    c.fillStyle = '#e08a62'; c.strokeStyle = '#5a2412'; c.lineWidth = 3;
    c.beginPath(); c.ellipse(0, 0, 44, 22, 0, 0, TAU); c.fill(); c.stroke();
    c.strokeStyle = 'rgba(255,225,200,.55)'; c.lineWidth = 3; c.beginPath(); c.moveTo(-26, -12); c.quadraticCurveTo(0, -17, 26, -12); c.stroke();
    // the twisted ends
    [-1, 1].forEach(function (sd) { c.fillStyle = '#c0694a'; c.beginPath(); c.moveTo(sd * 42, -6); c.lineTo(sd * 56, -10); c.lineTo(sd * 56, 10); c.lineTo(sd * 42, 6); c.closePath(); c.fill(); c.stroke(); });
    // face
    [-1, 1].forEach(function (sd) {
      c.fillStyle = '#fff'; c.strokeStyle = '#2a1230'; c.lineWidth = 2.4; c.beginPath(); c.ellipse(sd * 11 + 4, -3, 7, 8, 0, 0, TAU); c.fill(); c.stroke();
      c.strokeStyle = '#2a1230'; c.lineWidth = 1.6; c.beginPath(); for (var a = 0; a < 9; a += 0.5) c.lineTo(sd * 11 + 4 + Math.cos(a + T / 200) * a * 0.46, -3 + Math.sin(a + T / 200) * a * 0.46); c.stroke();
    });
    c.strokeStyle = '#2a1230'; c.lineWidth = 2.6; c.beginPath(); c.moveTo(-3, 9); c.lineTo(0, 12); c.lineTo(3, 9); c.lineTo(6, 12); c.lineTo(9, 9); c.stroke();
    // feet and a bent halo
    [-1, 1].forEach(function (sd) { c.fillStyle = skin.a; c.strokeStyle = '#2a1230'; c.lineWidth = 2.6; c.beginPath(); c.ellipse(sd * 12 + 60, sd * 5 + 6, 8, 5.6, 0, 0, TAU); c.fill(); c.stroke(); });
    c.save(); c.translate(6, -30 + Math.sin(T / 400) * 2); c.rotate(-0.3);
    c.strokeStyle = '#2a1230'; c.lineWidth = 7; c.beginPath(); c.ellipse(0, 0, 18, 5.4, 0, 0, TAU); c.stroke(); c.strokeStyle = '#ffd45a'; c.lineWidth = 3.6; c.beginPath(); c.ellipse(0, 0, 18, 5.4, 0, 0, TAU); c.stroke();
    c.restore();
    c.restore();
  }

  // ---- the hex-demon: a hulking purple brute rising out of the lava, drawn with the soul's lighting (a cool light from above,
  // a hot rim from the lava below): a muscled torso, a tapered arm with a real elbow and wrist, a clawed hand that pinches
  // the soul and flicks him away.
  var DEMON_SK = { hi: '#b8a0f4', a: '#7342bd', b: '#2c1460' };
  var DEMON_OUT = '#0d0516';

  // a gradient whose axis stays screen-vertical inside a context rotated by `ang` (and y-flipped when fl = -1)
  function skinGrad(c, sk, L, ang, fl, cx, cy, r) {
    var ux = -Math.sin(ang) * r, uy = -Math.cos(ang) * fl * r, g = c.createLinearGradient(cx + ux, cy + uy, cx - ux, cy - uy);
    skinStops(g, sk, L, false);
    return g;
  }

  // a limb segment from (x0, y0) to (x1, y1) whose half-widths follow prof = [[u, w], ...], shaded across its width
  function demonLimb(c, x0, y0, x1, y1, prof, sk, L) {
    var dx = x1 - x0, dy = y1 - y0, len = Math.sqrt(dx * dx + dy * dy) || 0.01, a = Math.atan2(dy, dx), n = 16, i, k, top = [], bot = [], wm = 0;
    function wAt(u) { for (k = 1; k < prof.length; k++) if (u <= prof[k][0]) return lerp(prof[k - 1][1], prof[k][1], smooth(prof[k - 1][0], prof[k][0], u)); return prof[prof.length - 1][1]; }
    c.save(); c.translate(x0, y0); c.rotate(a);
    for (i = 0; i <= n; i++) { var w = wAt(i / n); wm = Math.max(wm, w); top.push([i / n * len, -w]); bot.push([i / n * len, w]); }
    c.beginPath(); c.moveTo(top[0][0], top[0][1]);
    for (i = 1; i <= n; i++) c.lineTo(top[i][0], top[i][1]);
    for (i = n; i >= 0; i--) c.lineTo(bot[i][0], bot[i][1]);
    c.closePath(); c.fillStyle = skinGrad(c, sk, L, a, 1, len / 2, 0, wm); c.fill();
    c.lineJoin = 'round'; c.lineWidth = 2.2; c.strokeStyle = DEMON_OUT; c.stroke();
    var lo = Math.cos(a) >= 0 ? bot : top, hi = Math.cos(a) >= 0 ? top : bot;
    c.beginPath(); for (i = 1; i < n; i++) c[i === 1 ? 'moveTo' : 'lineTo'](lo[i][0], lo[i][1] * 0.9); c.strokeStyle = rimCol(L, 0.55); c.lineWidth = 1.6; c.stroke();     // the lava's rim light underneath
    c.beginPath(); for (i = 2; i < n - 1; i++) c[i === 2 ? 'moveTo' : 'lineTo'](hi[i][0], hi[i][1] * 0.78); c.strokeStyle = 'rgba(235,225,255,.22)'; c.lineWidth = 2; c.stroke();   // the sheen on top
    c.restore();
  }

  // a tapered capsule between two points, filled with the skin gradient and outlined
  function demonCap(c, x0, y0, x1, y1, w0, w1, g) {
    var a = Math.atan2(y1 - y0, x1 - x0), nx = -Math.sin(a), ny = Math.cos(a);
    c.beginPath();
    c.moveTo(x0 + nx * w0 / 2, y0 + ny * w0 / 2); c.lineTo(x1 + nx * w1 / 2, y1 + ny * w1 / 2);
    c.arc(x1, y1, w1 / 2, a + Math.PI / 2, a - Math.PI / 2, true);
    c.lineTo(x0 - nx * w0 / 2, y0 - ny * w0 / 2); c.arc(x0, y0, w0 / 2, a - Math.PI / 2, a - Math.PI * 1.5, true);
    c.closePath(); c.fillStyle = g; c.fill(); c.lineWidth = 1.9; c.strokeStyle = DEMON_OUT; c.stroke();
  }

  // a soft round muscle cap (shoulder, elbow): the same lighting as the limbs, only its outer arc (a0..a1) outlined
  function demonBall(c, x, y, r, sk, L, a0, a1) {
    var g = c.createLinearGradient(x, y - r, x, y + r); skinStops(g, sk, L, false);
    c.fillStyle = g; c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill();
    c.fillStyle = 'rgba(235,225,255,.15)'; c.beginPath(); c.ellipse(x - r * 0.2, y - r * 0.42, r * 0.5, r * 0.28, -0.3, 0, TAU); c.fill();
    c.beginPath(); c.arc(x, y, r * 0.86, 0.5, 2.6); c.strokeStyle = rimCol(L, 0.45); c.lineWidth = 1.4; c.stroke();
    if (a0 != null) { c.beginPath(); c.arc(x, y, r, a0, a1); c.lineWidth = 2.2; c.strokeStyle = DEMON_OUT; c.stroke(); }
  }

  // The demon's hand, drawn from the wrist (x, y). In its own frame it points along +x with the index finger on the -y side and the
  // thumb on +y (fl = -1 mirrors that for an arm that reaches left). o: {pinch 0..1: index and thumb close on the soul, flick 0..1}.
  // part: 'back' = the palm and the three curled fingers, 'front' = the index finger and thumb (drawn over the soul that they
  // pinch), anything else = all of it.
  var HAND_S = 1.1;
  function demonHand(c, x, y, ang, fl, sk, L, o, part) {
    var rel = clamp(o.flick * 3, 0, 1), curl = o.pinch * (1 - rel), snap = Math.sin(rel * Math.PI) * (1 - o.flick), i;
    c.save(); c.translate(x, y); c.rotate(ang); c.scale(HAND_S, fl * HAND_S); c.lineJoin = 'round'; c.lineCap = 'round';
    function G(cx, cy, r) { return skinGrad(c, sk, L, ang, fl, cx, cy, r); }
    function claw(px, py, a, hook, k) {
      var nx = -Math.sin(a), ny = Math.cos(a), ca = Math.cos(a), sa = Math.sin(a), bx = px + ca * 1.5, by = py + sa * 1.5;
      c.beginPath(); c.moveTo(bx + nx * 3.2 * k, by + ny * 3.2 * k);
      c.quadraticCurveTo(bx + ca * 7 * k + nx * 3 * hook * k, by + sa * 7 * k + ny * 3 * hook * k, bx + ca * 12 * k + nx * 7 * hook * k, by + sa * 12 * k + ny * 7 * hook * k);
      c.quadraticCurveTo(bx + ca * 6 * k - nx * 0.6, by + sa * 6 * k - ny * 0.6, bx - nx * 3.2 * k, by - ny * 3.2 * k); c.closePath();
      c.fillStyle = '#2a1238'; c.fill(); c.lineWidth = 1.3; c.strokeStyle = DEMON_OUT; c.stroke();
      c.beginPath(); c.moveTo(bx + nx * 1.4 * k, by + ny * 1.4 * k); c.lineTo(bx + ca * 8.5 * k + nx * 4.6 * hook * k, by + sa * 8.5 * k + ny * 4.6 * hook * k);
      c.strokeStyle = 'rgba(205,175,240,.5)'; c.lineWidth = 0.9; c.stroke();
    }
    // a finger of tapered phalanges from (bx, by): first angle a1, then the bends; ws = the widths at the joints; the claw hooks to `hook`
    function finger(bx, by, lens, a1, bends, ws, hook, k) {
      var px = bx, py = by, a = a1, j;
      for (j = 0; j < lens.length; j++) {
        if (j) a += bends[j - 1];
        var nx = px + Math.cos(a) * lens[j], ny = py + Math.sin(a) * lens[j];
        demonCap(c, px, py, nx, ny, ws[j], ws[j + 1], G((px + nx) / 2, (py + ny) / 2, Math.max(ws[j], ws[j + 1]) * 0.62));
        c.strokeStyle = 'rgba(235,225,255,.2)'; c.lineWidth = 1.2; c.beginPath();                                          // a sheen along the top of the phalanx
        c.moveTo(px + Math.sin(a) * ws[j] * 0.22 + Math.cos(a) * 2, py - Math.cos(a) * ws[j] * 0.22 + Math.sin(a) * 2); c.lineTo(nx + Math.sin(a) * ws[j + 1] * 0.22 - Math.cos(a) * 2, ny - Math.cos(a) * ws[j + 1] * 0.22 - Math.sin(a) * 2); c.stroke();
        px = nx; py = ny;
      }
      claw(px, py, a, hook, k);
    }
    if (part !== 'front') {
      // the palm and the back of the hand
      c.beginPath(); c.moveTo(-3, -13); c.bezierCurveTo(14, -18, 32, -21, 48, -19); c.lineTo(52, -4); c.lineTo(50, 14); c.bezierCurveTo(40, 21, 24, 28, 8, 22); c.bezierCurveTo(2, 20, -2, 16, -3, 13); c.closePath();
      c.fillStyle = G(20, 2, 26); c.fill(); c.lineWidth = 2.2; c.strokeStyle = DEMON_OUT; c.stroke();
      c.strokeStyle = 'rgba(14,6,22,.32)'; c.lineWidth = 1.1;                                                                // the tendons on the back of the hand
      for (i = 0; i < 4; i++) { c.beginPath(); c.moveTo(4, -8 + i * 6); c.quadraticCurveTo(26, -11 + i * 7, 46, -10 + i * 8); c.stroke(); }
      // the middle, ring and little fingers: spread and relaxed, then curled into the palm as he pinches
      var cf = [[46, 15, [15, 12, 9.5], 0.16, [12, 10.6, 9, 7.6]], [49, 7, [19, 15, 11.5], 0.0, [13, 11.6, 10, 8.4]], [50, -2, [21, 16, 12.5], -0.17, [14, 12.6, 11, 9]]];
      for (i = 0; i < 3; i++) {
        var q = cf[i];
        finger(q[0], q[1], q[2], lerp(q[3], 0.95, curl) - 0.18 * snap, [lerp(0.1, 1.0, curl), lerp(0.12, 0.95, curl)], q[4], 1, 0.85);
      }
      c.fillStyle = 'rgba(235,225,255,.28)'; for (i = 0; i < 3; i++) { c.beginPath(); c.arc(cf[2 - i][0] - 1, cf[2 - i][1] - 6, 2.2, 0, TAU); c.fill(); }
    }
    if (part !== 'back') {
      // the index finger (open -> pinched -> snapping out) and the thumb
      finger(48, -11, [20, 16, 12], lerp(-0.34, 0.0, curl) - 0.55 * snap, [lerp(-0.04, 0.3, curl) - 0.18 * snap, lerp(0.04, 0.55, curl) - 0.2 * snap], [14.5, 12.5, 10.5, 8], 1, 1);
      finger(27, 12, [29, 27], lerp(0.55, -0.05, curl) + 0.1 * snap, [lerp(-0.05, -0.27, curl)], [18, 14.5, 10], -1, 1);
    }
    c.restore();
  }

  // where the demon's shoulder, elbow and wrist are: the arm reaches out to the left to the hand that pinches the soul at (hx, hy)
  function demonPose(x, y, o) {
    var rise = easeOut(o.rise), by = y + (1 - rise) * 230, sx = x - 70, sy = by - 84;
    var A = ik(sx, sy, o.hx - 2 + 88 * HAND_S, o.hy + 2 + 4.8 * HAND_S, 82, 80, 2);
    return { by: by, sx: sx, sy: sy, A: A, ang: Math.PI + 0.1 };
  }

  // the index finger and thumb, drawn over the soul they pinch
  function bigDemonFront(I, c, th, x, y, o, T) {
    var P = demonPose(x, y, o);
    demonHand(c, P.A.hx, P.A.hy, P.ang, -1, DEMON_SK, { glow: th.glow, rim: 0.9, rot: 0 }, o, 'front');
  }

  function bigDemon(I, c, th, x, y, o, T) {
    // The hex-demon: rises out of the lava on the right (x, y = where his belly meets the lava, screen px).
    // o: {rise, hx, hy (screen: where the soul is pinched), pinch 0..1, flick 0..1, smug 0..1}
    var P = demonPose(x, y, o), by = P.by, out = DEMON_OUT, sk = DEMON_SK, L = { glow: th.glow, rim: 0.9, rot: 0 }, i, sd;
    c.save();
    c.translate(x, by);
    c.lineJoin = 'round'; c.lineCap = 'round';
    // ---- the torso: shoulders and trapezius, pecs, ribs, a gut that melts into the lava
    function torsoPath() {
      c.beginPath(); c.moveTo(-92, 24);
      c.bezierCurveTo(-98, -30, -98, -72, -78, -98); c.bezierCurveTo(-66, -112, -44, -116, -24, -126); c.lineTo(24, -126);
      c.bezierCurveTo(44, -116, 66, -112, 78, -98); c.bezierCurveTo(98, -72, 98, -30, 92, 24); c.closePath();
    }
    torsoPath(); var tg = c.createLinearGradient(0, -126, 0, 24); skinStops(tg, sk, L, false); c.fillStyle = tg; c.fill();
    c.save(); torsoPath(); c.clip();
    var sh = c.createLinearGradient(-98, 0, 98, 0); sh.addColorStop(0, 'rgba(10,4,24,.55)'); sh.addColorStop(0.28, 'rgba(10,4,24,0)'); sh.addColorStop(0.72, 'rgba(10,4,24,0)'); sh.addColorStop(1, 'rgba(10,4,24,.55)');
    c.fillStyle = sh; c.fillRect(-100, -130, 200, 160);                                                    // roundness: the flanks fall into shadow
    var hl = c.createRadialGradient(-26, -88, 4, -26, -88, 70); hl.addColorStop(0, 'rgba(235,225,255,.22)'); hl.addColorStop(1, 'rgba(235,225,255,0)');
    c.fillStyle = hl; c.fillRect(-100, -130, 200, 160);
    var lv = c.createLinearGradient(0, -22, 0, 24); lv.addColorStop(0, 'rgba(' + th.glow + ',0)'); lv.addColorStop(1, 'rgba(' + th.glow + ',.7)');
    c.fillStyle = lv; c.fillRect(-100, -24, 200, 50);                                                      // lit from below by the lava
    for (sd = -1; sd <= 1; sd += 2) {
      c.strokeStyle = 'rgba(12,4,26,.55)'; c.lineWidth = 2.4;
      c.beginPath(); c.moveTo(sd * 4, -74); c.quadraticCurveTo(sd * 36, -56, sd * 66, -78); c.stroke();      // the underside of each pec
      c.beginPath(); c.moveTo(sd * 10, -112); c.lineTo(sd * 54, -106); c.stroke();                           // the collarbones
      c.strokeStyle = 'rgba(235,225,255,.2)'; c.lineWidth = 1.4;
      c.beginPath(); c.moveTo(sd * 4, -71); c.quadraticCurveTo(sd * 36, -53, sd * 66, -75); c.stroke();
      c.beginPath(); c.moveTo(sd * 10, -110); c.lineTo(sd * 54, -104); c.stroke();
      c.strokeStyle = 'rgba(12,4,26,.35)'; c.lineWidth = 2;
      for (var r = 0; r < 3; r++) { c.beginPath(); c.moveTo(sd * 4, -42 + r * 15); c.quadraticCurveTo(sd * 20, -38 + r * 15, sd * 30, -44 + r * 15); c.stroke(); }     // the abs
    }
    c.strokeStyle = 'rgba(12,4,26,.3)'; c.lineWidth = 2; c.beginPath(); c.moveTo(0, -60); c.lineTo(0, 22); c.stroke();
    c.restore();
    torsoPath(); c.lineWidth = 3.4; c.strokeStyle = out; c.stroke();
    // the gold chain and the hex on his chest
    c.strokeStyle = '#7a5410'; c.lineWidth = 3.2; c.beginPath(); c.moveTo(-26, -122); c.quadraticCurveTo(-30, -78, 0, -88); c.quadraticCurveTo(30, -78, 26, -122); c.stroke();
    c.strokeStyle = '#e8b83a'; c.lineWidth = 1.7; c.stroke();
    c.beginPath(); for (i = 0; i < 6; i++) { var a = i / 6 * TAU + TAU / 12; c.lineTo(Math.cos(a) * 26, -62 + Math.sin(a) * 26); } c.closePath();
    var hg = c.createLinearGradient(0, -90, 0, -34); hg.addColorStop(0, '#ffe98a'); hg.addColorStop(0.55, '#e6b02e'); hg.addColorStop(1, '#a56f12');
    c.fillStyle = hg; c.fill(); c.strokeStyle = out; c.lineWidth = 3.2; c.stroke();
    c.beginPath(); for (i = 0; i < 6; i++) { var a2 = i / 6 * TAU + TAU / 12; c.lineTo(Math.cos(a2) * 20, -62 + Math.sin(a2) * 20); } c.closePath(); c.strokeStyle = 'rgba(255,248,200,.5)'; c.lineWidth = 1.2; c.stroke();
    c.strokeStyle = '#5a3606'; c.lineWidth = 3.2; c.beginPath(); c.moveTo(-9, -74); c.lineTo(-9, -50); c.moveTo(9, -74); c.lineTo(9, -50); c.moveTo(-9, -62); c.lineTo(9, -62); c.stroke();
    // neck
    c.fillStyle = mixc(sk.a, sk.b, 0.35); c.strokeStyle = out; c.lineWidth = 3; c.beginPath(); c.moveTo(-26, -124); c.lineTo(-22, -152); c.lineTo(34, -152); c.lineTo(38, -124); c.closePath(); c.fill(); c.stroke();
    // ---- the head
    c.save(); c.translate(6, -160); c.rotate(Math.sin(T / 600) * 0.03 + o.smug * 0.06);
    for (sd = -1; sd <= 1; sd += 2) {
      // the horns: ridged, dark at the root, paler at the tip
      var hgr = c.createLinearGradient(sd * 24, -30, sd * 40, -100); hgr.addColorStop(0, '#1b0d2e'); hgr.addColorStop(0.6, '#4a3566'); hgr.addColorStop(1, '#a692c4');
      c.fillStyle = hgr; c.strokeStyle = out; c.lineWidth = 2.6;
      c.beginPath(); c.moveTo(sd * 22, -30); c.bezierCurveTo(sd * 62, -38, sd * 66, -76, sd * 40, -104); c.bezierCurveTo(sd * 42, -74, sd * 38, -52, sd * 10, -40); c.closePath(); c.fill(); c.stroke();
      c.strokeStyle = 'rgba(10,4,20,.55)'; c.lineWidth = 1.4;
      for (var rg = 0; rg < 4; rg++) { var ry = -42 - rg * 13; c.beginPath(); c.moveTo(sd * (24 + 9 * rg * 0.5), ry + 3); c.lineTo(sd * (40 + 7 * rg * 0.6 - rg), ry - 3); c.stroke(); }
      // the ears: pointed, with a darker hollow
      var eg2 = c.createLinearGradient(0, -20, 0, 22); skinStops(eg2, sk, L, false); c.fillStyle = eg2; c.strokeStyle = out; c.lineWidth = 3;
      c.beginPath(); c.moveTo(sd * 42, -6); c.lineTo(sd * 84, -20); c.lineTo(sd * 50, 20); c.closePath(); c.fill(); c.stroke();
      c.fillStyle = 'rgba(20,6,34,.55)'; c.beginPath(); c.moveTo(sd * 48, -2); c.lineTo(sd * 72, -13); c.lineTo(sd * 52, 12); c.closePath(); c.fill();
    }
    c.beginPath(); c.moveTo(-50, -8); c.bezierCurveTo(-52, -36, -26, -48, 0, -48); c.bezierCurveTo(26, -48, 52, -36, 50, -8); c.bezierCurveTo(50, 22, 28, 46, 0, 46); c.bezierCurveTo(-28, 46, -50, 22, -50, -8); c.closePath();
    var hd = c.createLinearGradient(0, -48, 0, 46); skinStops(hd, sk, L, false); c.fillStyle = hd; c.fill();
    c.save(); c.clip();
    var hs = c.createLinearGradient(-52, 0, 52, 0); hs.addColorStop(0, 'rgba(10,4,24,.5)'); hs.addColorStop(0.3, 'rgba(10,4,24,0)'); hs.addColorStop(0.7, 'rgba(10,4,24,0)'); hs.addColorStop(1, 'rgba(10,4,24,.5)');
    c.fillStyle = hs; c.fillRect(-54, -50, 108, 98);
    c.fillStyle = 'rgba(20,6,34,.28)'; for (sd = -1; sd <= 1; sd += 2) { c.beginPath(); c.ellipse(sd * 32, 14, 10, 14, sd * 0.3, 0, TAU); c.fill(); }      // hollow cheeks
    c.fillStyle = 'rgba(235,225,255,.16)'; c.beginPath(); c.ellipse(-8, -34, 24, 8, -0.1, 0, TAU); c.fill();                                       // forehead sheen
    c.restore();
    c.beginPath(); c.moveTo(-50, -8); c.bezierCurveTo(-52, -36, -26, -48, 0, -48); c.bezierCurveTo(26, -48, 52, -36, 50, -8); c.bezierCurveTo(50, 22, 28, 46, 0, 46); c.bezierCurveTo(-28, 46, -50, 22, -50, -8); c.closePath();
    c.lineWidth = 3.6; c.strokeStyle = out; c.stroke();
    var lid = o.smug > 0 ? 0.55 : 0.3;
    for (sd = -1; sd <= 1; sd += 2) {
      var eg = c.createRadialGradient(sd * 21, -6, 1, sd * 21, -6, 24); eg.addColorStop(0, 'rgba(255,225,90,.38)'); eg.addColorStop(1, 'rgba(255,225,90,0)'); c.fillStyle = eg; c.fillRect(sd * 21 - 26, -32, 52, 52);
      var ey = c.createRadialGradient(sd * 21 - 2, -8, 1, sd * 21, -6, 13); ey.addColorStop(0, '#fff7b0'); ey.addColorStop(0.7, '#ffd23a'); ey.addColorStop(1, '#d9901a');
      c.fillStyle = ey; c.strokeStyle = out; c.lineWidth = 2.6; c.beginPath(); c.ellipse(sd * 21, -6, 13, 12, 0, 0, TAU); c.fill(); c.stroke();
      c.fillStyle = out; c.beginPath(); c.ellipse(sd * 21, -6, 3.2, 10, 0, 0, TAU); c.fill();
      c.save(); c.beginPath(); c.ellipse(sd * 21, -6, 12.5, 11.5, 0, 0, TAU); c.clip();
      c.fillStyle = mixc(sk.a, sk.b, 0.2); c.fillRect(sd * 21 - 14, -20, 28, 16 * lid); c.fillStyle = 'rgba(0,0,0,.35)'; c.fillRect(sd * 21 - 14, -20 + 16 * lid - 1.5, 28, 2.4);       // heavy lids: bored
      c.restore();
      c.strokeStyle = out; c.lineWidth = 6.4; c.beginPath(); c.moveTo(sd * 38, -25 - o.smug * 3); c.lineTo(sd * 7, -15 + o.smug * 2); c.stroke();     // the brow ridge
      c.strokeStyle = 'rgba(235,225,255,.28)'; c.lineWidth = 1.6; c.beginPath(); c.moveTo(sd * 36, -29 - o.smug * 3); c.lineTo(sd * 10, -19 + o.smug * 2); c.stroke();
    }
    c.fillStyle = 'rgba(14,5,26,.65)'; for (sd = -1; sd <= 1; sd += 2) { c.beginPath(); c.ellipse(sd * 5, 5, 2.6, 3.4, sd * 0.3, 0, TAU); c.fill(); }          // nostrils
    var mo = o.smug > 0 ? 0.2 : 0.55;
    c.fillStyle = '#44070f'; c.strokeStyle = out; c.lineWidth = 3.4; c.beginPath(); c.moveTo(-27, 14); c.quadraticCurveTo(0, 14 + 36 * mo, 27, 14); c.quadraticCurveTo(0, 18, -27, 14); c.closePath(); c.fill(); c.stroke();
    c.fillStyle = '#f4ecdc'; c.strokeStyle = 'rgba(20,6,30,.8)'; c.lineWidth = 1; [-16, -5, 6, 17].forEach(function (fx) { c.beginPath(); c.moveTo(fx - 4, 15); c.lineTo(fx + 4, 15); c.lineTo(fx, 25); c.closePath(); c.fill(); c.stroke(); });
    c.restore();
    // the deltoid of the far shoulder
    demonBall(c, 70, -86, 27, sk, L, Math.PI * 1.1, Math.PI * 1.9);
    c.restore();
    // ---- the arm: upper arm, deltoid, forearm, elbow, a gold bracer, then the hand
    var sx = P.sx, sy = P.sy, A = P.A;
    demonLimb(c, sx, sy, A.ex, A.ey, [[0, 27], [0.3, 30], [0.62, 26], [1, 19]], sk, L);
    demonBall(c, sx, sy, 28, sk, L, Math.PI * 0.85, Math.PI * 1.75);                                         // the deltoid caps the top of the upper arm
    c.strokeStyle = 'rgba(12,4,26,.4)'; c.lineWidth = 2; c.beginPath(); c.moveTo(sx - 18, sy + 26); c.quadraticCurveTo(sx - 26, sy + 38, sx - 28, sy + 50); c.stroke();    // where the bicep meets the shoulder
    demonLimb(c, A.ex, A.ey, A.hx, A.hy, [[0, 20], [0.25, 23.5], [0.62, 17.5], [1, 13.5]], sk, L);
    demonBall(c, A.ex, A.ey, 14, sk, L, -0.4, Math.PI * 0.8);                                                // the elbow
    var fa = Math.atan2(A.hy - A.ey, A.hx - A.ex);
    c.save(); c.translate(A.hx, A.hy); c.rotate(fa);
    var bg = c.createLinearGradient(0, -16, 0, 16); bg.addColorStop(0, '#ffe98a'); bg.addColorStop(0.5, '#d9a22a'); bg.addColorStop(1, '#8a5a0c');
    c.fillStyle = bg; c.strokeStyle = out; c.lineWidth = 2.4; rr(c, -15, -16, 17, 32, 5); c.fill(); c.stroke();
    c.strokeStyle = 'rgba(255,248,200,.5)'; c.lineWidth = 1.2; c.beginPath(); c.moveTo(-12, -11); c.lineTo(-1, -11); c.stroke();
    c.restore();
    // the hand points left, a little downward, mirrored so that the index finger stays on top; its palm and curled fingers are here, the pinch is drawn over the soul
    demonHand(c, A.hx, A.hy, P.ang, -1, sk, L, o, 'back');
  }


  function trampDraw(I, c, th, x, y, arr, T, sag, sc) {
    var bx = x + (1 - arr) * 440, out = '#0b0710';
    c.save(); c.translate(bx, y);
    // bone posts
    [-92, 104].forEach(function (px) {
      c.strokeStyle = out; c.lineWidth = 13; c.lineCap = 'round'; c.beginPath(); c.moveTo(px, 70); c.lineTo(px, -6); c.stroke();
      c.strokeStyle = th.bone; c.lineWidth = 8; c.beginPath(); c.moveTo(px, 70); c.lineTo(px, -6); c.stroke();
      c.fillStyle = th.bone; c.strokeStyle = out; c.lineWidth = 2.6; c.beginPath(); c.arc(px - 4, -10, 7, 0, TAU); c.arc(px + 4, -10, 7, 0, TAU); c.fill(); c.stroke();
    });
    // the membrane: a bat wing stretched between the posts, sagging
    c.fillStyle = '#4a2a52'; c.strokeStyle = out; c.lineWidth = 3.4;
    c.beginPath(); c.moveTo(-92, -4);
    for (var i = 1; i <= 24; i++) { var u = i / 24, mx = lerp(-92, 104, u); c.lineTo(mx, -4 + Math.sin(u * Math.PI) * sag); }
    for (var j = 24; j >= 0; j--) { var u2 = j / 24, mx2 = lerp(-92, 104, u2); c.lineTo(mx2, 12 + Math.sin(u2 * Math.PI) * sag * 0.9 + (j % 2 ? 7 : 0)); }
    c.closePath(); c.fill(); c.stroke();
    c.strokeStyle = 'rgba(255,255,255,.25)'; c.lineWidth = 2; for (var k = 1; k < 6; k++) { var uk = k / 6; c.beginPath(); c.moveTo(lerp(-92, 104, uk), -4 + Math.sin(uk * Math.PI) * sag); c.lineTo(lerp(-92, 104, uk), 12 + Math.sin(uk * Math.PI) * sag * 0.9); c.stroke(); }
    // the sign
    c.fillStyle = '#ffd24a'; c.strokeStyle = out; c.lineWidth = 2.4; c.beginPath(); rr(c, -52, 24, 120, 22, 5); c.fill(); c.stroke();
    c.fillStyle = '#3a1a06'; c.font = '900 12px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('BOING-O-RAMA', 8, 36);
    c.restore();
  }

  function umbrellaDraw(I, c, th, x, y, o, T) {
    // (x, y) = where his right hand holds the shaft; o = {open, burn}. A striped dome that opens over his head, floats,
    // and then catches fire from the heat below
    var op = o.open, burn = o.burn, cw = 92 * op, ch = 48 * op, edge = -(70 + 38 * op);
    c.save(); c.translate(x, y);
    // the shaft with its hooked handle, from below his hand up to the top of the dome
    c.lineCap = 'round'; c.lineJoin = 'round';
    c.strokeStyle = '#2a1230'; c.lineWidth = 7.5; c.beginPath(); c.moveTo(-9, 40); c.quadraticCurveTo(-11, 50, -2, 47); c.moveTo(0, 38); c.lineTo(0, edge - ch); c.stroke();
    c.strokeStyle = '#a06a30'; c.lineWidth = 3.6; c.beginPath(); c.moveTo(-9, 40); c.quadraticCurveTo(-11, 50, -2, 47); c.moveTo(0, 38); c.lineTo(0, edge - ch); c.stroke();
    c.translate(0, edge);
    var n = 6, i;
    for (i = 0; i < n; i++) {
      var x0 = -cw + 2 * cw * i / n, x1 = -cw + 2 * cw * (i + 1) / n, xm = (x0 + x1) / 2;
      var char = clamp((burn * 1.5 - 0.5 * Math.abs(i - 2.5) / 2.5), 0, 1);
      if (char >= 1) continue;
      var base = i % 2 ? '#f4efe6' : '#e23a3a';
      c.fillStyle = char > 0.05 ? mixc(base, '#150a06', char) : base; c.strokeStyle = '#2a1230'; c.lineWidth = 2.6;
      c.beginPath(); c.moveTo(0, -ch);
      c.quadraticCurveTo(x0 * 0.9, -ch * 0.55, x0, 4);
      c.quadraticCurveTo(xm, 14, x1, 4);                              // a scalloped edge
      c.quadraticCurveTo(x1 * 0.9, -ch * 0.55, 0, -ch); c.closePath(); c.fill(); c.stroke();
    }
    c.fillStyle = '#2a1230'; c.beginPath(); c.arc(0, -ch, 3.4, 0, TAU); c.fill();
    if (burn > 0.05 && burn < 0.98) {                                 // flames along the edge
      for (var f = 0; f < 11; f++) {
        var fx = -cw + 2 * cw * (f + 0.5) / 11, fy = 6, fh = (16 + 12 * Math.sin(T / 60 + f * 2)) * burn;
        c.fillStyle = th.lava0; c.beginPath(); c.moveTo(fx - 7, fy); c.quadraticCurveTo(fx - 6, fy - fh * 0.6, fx, fy - fh); c.quadraticCurveTo(fx + 6, fy - fh * 0.6, fx + 7, fy); c.closePath(); c.fill();
        c.fillStyle = th.lava2; c.beginPath(); c.moveTo(fx - 3.4, fy); c.quadraticCurveTo(fx, fy - fh * 0.6, fx + 3.4, fy); c.closePath(); c.fill();
      }
    }
    c.restore();
  }

  function lavaSplash(c, th, x, ys, age, seed) {
    if (age < 0 || age > 1600) return;
    ring(c, x, ys + 2, age, 1300, 6, 90, th.lava2, 5);
    ring(c, x, ys + 2, age - 160, 1200, 4, 60, th.lava1, 4);
    burst(c, x, ys, age, { n: 26, seed: seed || 3, speed: 340, g: 900, life: 1300, size: 4.2, colors: [th.lava1, th.lava2, th.lava0], spread: 1.9, shrink: true });
    textPop(c, th, 'SPLASH!', x, ys - 70, age - 80, 1000, th.lava2, 30, -0.08);
  }

  // the hex-demon of the 'flick' finale: his body and arm sit behind the soul, his pinching finger and thumb in front of it
  function flickDemon(I, c, th, fi, pose, S, T, front) {
    var d = S(DEMON.x + 232, LAVA_Y + 6), fx = pose.fx && pose.fx.flick, hp = S(DEMON.x - 38 + 6, DEMON.handY + 18);
    if (d.y >= VH + 320) return;
    var o = { rise: fi.arr, hx: hp.x, hy: hp.y, pinch: fx ? (fx.post < 0.24 ? smooth(0, 0.06, fx.post) : 0) : 0.0, flick: fx ? fx.flick : 0, smug: fx && fx.post > 0.5 ? 1 : 0 };
    (front ? bigDemonFront : bigDemon)(I, c, th, d.x, d.y, o, T);
  }

  DRAW.finaleBack = function (I, c, sc, T) {
    var fi = finaleInfo(sc), th = I.th, pose = sc.pose;
    if (!fi || !pose) return;
    var cy = I.cam.y;
    function S(wx, wy) { return { x: LANE_X + wx, y: ANCHOR_Y - (wy - cy) }; }
    if (fi.style === 'cauldron') { var p = S(CAULDRON.x, CAULDRON.rim); if (p.y < VH + 140) cauldronBack(I, c, th, p.x, p.y, fi.arr, T, sc); }
    else if (fi.style === 'boing') { var q = S(TRAMP.x, TRAMP.y); if (q.y < VH + 140) { var f = pose.fx && pose.fx.boing; var sag = 4; if (f) { var cf = Math.min(f.f, 1 - f.f); sag = 4 + (f.post < 0.94 ? 30 * Math.exp(-f.f * 9) * Math.pow(0.6, f.k) : 4); } trampDraw(I, c, th, q.x, q.y, fi.arr, T, sag, sc); } }
    else if (fi.style === 'grinder') { var g = S(GRIND.x, GRIND.hopperY + 10); if (g.y < VH + 220) grinderDraw(I, c, th, g.x, g.y, fi.arr, T, { post: pose.fx && pose.fx.grind ? pose.fx.grind.post : 0, d: fi.b.d }); }
    else if (fi.style === 'flick') {
      flickDemon(I, c, th, fi, pose, S, T, false);
    }
    else if (fi.style === 'umbrella' && pose.fx && pose.fx.umb && !pose.hidden) {
      var sp = I.soulScreen, u = pose.fx.umb, gh = pose.hands[1]; if (u.open > 0 && u.burn < 0.995 && u.u < FALL_IMPACT.umbrella + 0.02) umbrellaDraw(I, c, th, sp.x + gh.x - pose.x, sp.y - (gh.y - pose.y), u, T);
    }
  };

  DRAW.finaleFront = function (I, c, sc, T) {
    var fi = finaleInfo(sc), th = I.th, pose = sc.pose, tl = sc.tl;
    if (!fi || !pose) {
      if (tl && tl.escapeBeat) DRAW.confetti(I, c, sc, T);
      return;
    }
    var cy = I.cam.y;
    function S(wx, wy) { return { x: LANE_X + wx, y: ANCHOR_Y - (wy - cy) }; }
    var ms = sc.tms - fi.b.t, imp = FALL_IMPACT[fi.style] || 0.55, impMs = imp * fi.b.d;
    if (fi.style === 'cauldron') {
      var p = S(CAULDRON.x, CAULDRON.rim);
      if (p.y < VH + 140) {
        cauldronFront(I, c, th, p.x, p.y, fi.arr, T, 0.6 + 0.4 * (ms > impMs ? 1 : 0));
        if (ms > impMs) { burst(c, p.x, p.y, ms - impMs, { n: 22, seed: 7, speed: 260, g: 800, life: 1100, size: 4, colors: ['#9be05a', '#d7ff9a', '#5aa13a'], spread: 2.2, shrink: true }); textPop(c, th, 'SPLASH!', p.x, p.y - 90, ms - impMs - 60, 900, '#d7ff9a', 30, 0.06); }
      }
    }
    else if (fi.style === 'yelp') {
      var ys = ANCHOR_Y - (LAVA_Y - cy);
      if (ms > impMs && ys < VH + 100) {
        lavaSplash(c, th, LANE_X + 6, ys, ms - impMs, 9);
        if (fi.post > 0.78) {                                    // all that is left: the halo, floating
          var hy = ys + 4 + Math.sin(T / 500) * 2.4;
          c.save(); c.translate(LANE_X + 6, hy);
          c.strokeStyle = '#2a1230'; c.lineWidth = 8; c.beginPath(); c.ellipse(0, 0, 19, 5.6, 0, 0, TAU); c.stroke(); c.strokeStyle = '#ffd45a'; c.lineWidth = 4.2; c.beginPath(); c.ellipse(0, 0, 19, 5.6, 0, 0, TAU); c.stroke(); c.restore();
          ring(c, LANE_X + 6, ys + 6, T % 1800, 1800, 12, 54, 'rgba(255,200,120,.8)', 2.4);
        }
      }
    }
    else if (fi.style === 'bonk') {
      var bk = pose.fx && pose.fx.bonk, n = fi.b.n || 1, lv = fi.b.lv;
      for (var k = 0; k < n; k++) {
        var bt = k === n - 1 ? imp * fi.b.d : imp * fi.b.d * (k + 1) / n, since = ms - bt;
        var lvK = lv * (1 - (k + 1) / n), xK = k === n - 1 ? -128 : ((k % 2) ? -34 : 34);
        if (since > -260 && since < 1500) {
          var pp = S(xK, lvK * LV + (k === n - 1 ? 2 : 0)), grow = ease((since + 260) / 220);
          if (k < n - 1) { c.save(); c.translate(pp.x, pp.y); c.scale(grow, grow); slab(c, 0, 0, 78, 14, th, '#8a6a60'); c.restore(); }
          if (since > 0 && since < 1100) {
            burst(c, pp.x, pp.y - 24, since, { n: 8, seed: k * 11 + 1, speed: 180, g: 400, life: 800, size: 6, colors: ['#ffe14a', '#fff6a0'], shape: 'star', spread: 5.6, dir: -Math.PI / 2 });
            textPop(c, th, k === n - 1 ? 'BONK!!' : (k % 2 ? 'BONK!' : 'OW!'), pp.x + (xK > 0 ? -50 : 50), pp.y - 64, since - 20, 800, '#ffe14a', 26, xK > 0 ? -0.15 : 0.15);
          }
        }
      }
      if (bk === undefined) { }
      if (pose.fx && pose.fx.stars) {                           // stars circling his head on the platform
        var sp2 = I.soulScreen;
        for (var s = 0; s < 4; s++) { var a = T / 300 + s * 1.57; c.fillStyle = '#ffe14a'; star(c, sp2.x + 40 + Math.cos(a) * 28, sp2.y - 40 + Math.sin(a) * 9, 7, 5, a); }
      }
    }
    else if (fi.style === 'grinder') {
      var g = S(GRIND.x, GRIND.hopperY + 10), post = pose.fx && pose.fx.grind ? pose.fx.grind.post : 0;
      if (post > 0.84 && g.y < VH + 220) {                      // the sausage on the tray
        var sx = g.x - 109 + (1 - fi.arr) * 440, sy = g.y + 70;
        drawSausage(c, th, sx, sy, SKINS[((sc.g && sc.g.soul && sc.g.soul.skin) | 0) % SKINS.length], T, { rot: 0 });
      }
    }
    else if (fi.style === 'flick') {
      var fx = pose.fx && pose.fx.flick;
      flickDemon(I, c, th, fi, pose, S, T, true);
      if (fx && fx.post > 0.24 && fx.post < 0.7) {
        var fp = S(DEMON.x - 38 + 6, DEMON.handY + 18), age = (fx.post - 0.24) * fi.b.d;
        ring(c, fp.x, fp.y, age, 500, 6, 60, '#fff', 4);
        textPop(c, th, 'FLICK!', fp.x + 10, fp.y - 70, age, 900, '#ffe14a', 32, -0.1);
        if (age > 900) { var tp = S(DEMON.x + 330, 330); star(c, tp.x - 40, tp.y, 14, 4, T / 400); }
      }
    }
    else if (fi.style === 'boing') {
      var fb = pose.fx && pose.fx.boing;
      if (fb && fb.post < 0.94) { var tp2 = S(TRAMP.x, TRAMP.y), cf = fb.f; if (cf < 0.16) textPop(c, th, 'BOING!', tp2.x + 40, tp2.y - 80 - fb.k * 14, cf * fi.b.d * (1 - imp) / 3.2, 520, '#d7ff9a', 26 - fb.k * 3, 0.1); }
    }
    else if (fi.style === 'umbrella') {
      var um = pose.fx && pose.fx.umb;
      if (um && um.u > imp) {
        var sp3 = I.soulScreen, ageU = (um.u - imp) * fi.b.d;
        burst(c, sp3.x, sp3.y - 20, ageU, { n: 14, seed: 4, speed: 120, g: -80, life: 1600, size: 7, colors: ['rgba(60,50,50,.5)', 'rgba(90,80,80,.4)'], spread: 3, dir: -Math.PI / 2 });
        // the charred ribs left on his head
        c.strokeStyle = '#1a1014'; c.lineWidth = 2.4; c.lineCap = 'round';
        for (var r = 0; r < 5; r++) { var ar = Math.PI + (r + 0.5) / 5 * Math.PI; c.beginPath(); c.moveTo(sp3.x, sp3.y - 120); c.lineTo(sp3.x + Math.cos(ar) * 44, sp3.y - 120 + Math.sin(ar) * 22 + 10); c.stroke(); }
      }
    }
    if (tl && tl.escapeBeat) DRAW.confetti(I, c, sc, T);
  };
  function arr0(a) { return a; }

  // ==================================================================== escape: confetti and the sun
  DRAW.confetti = function (I, c, sc, T) {
    var tl = sc.tl, eb = tl && tl.escapeBeat;
    if (!eb) return;
    var age = sc.tms - (eb.t + eb.d * 0.58);
    if (age < 0) return;
    var cols = ['#ff4d6d', '#ffd23f', '#4dd2ff', '#7dff9a', '#c77dff', '#ff9a3c'];
    for (var i = 0; i < 70; i++) {
      var r1 = hash(i * 5 + 1), r2 = hash(i * 5 + 2), r3 = hash(i * 5 + 3), delay = r1 * 700, a = age - delay;
      if (a < 0) continue;
      var spd = 60 + r2 * 120, fall = (a / 1000) * (90 + r3 * 110), x = LANE_X + 18 + (r2 - 0.5) * 500 + Math.sin(a / 300 + i) * 24, y = (I.soulScreen.y - 120) - Math.sin(clamp(a / 500, 0, 1) * Math.PI / 2) * (140 + r1 * 120) + fall;
      if (y > VH + 20) continue;
      c.save(); c.translate(x, y); c.rotate(a / 180 + i); c.fillStyle = cols[i % cols.length]; c.globalAlpha = clamp(1 - a / 6000, 0, 1) ; c.fillRect(-4, -2.4, 8, 4.8 * (0.4 + 0.6 * Math.abs(Math.sin(a / 140 + i)))); c.restore();
    }
  };

  // ==================================================================== for the editor: a sample game + a demo climb
  // (display only: the real odds and every real script come from the server - climb.py)
  function survivalCurve(H, escapePct) {
    var big = -Math.log(clamp(+escapePct || 2, 0.1, 50) / 100), s = [1];
    for (var h = 1; h <= H; h++) { var t = h / H; s.push(Math.exp(-big * (t + 0.9 * t * t) / 1.9)); }
    return s;
  }
  function multAt(s, h, edgePct) { return Math.max(1, Math.floor(100 * (1 - clamp(+edgePct || 0, 0, 25) / 100) / s[h] + 1e-6) / 100); }
  function multsOf(H, escapePct, edgePct) {
    var s = survivalCurve(H, escapePct), out = [];
    for (var h = 1; h <= H; h++) out.push(multAt(s, h, edgePct));
    return out;
  }
  function ladderOf(H, escapePct, edgePct) {
    var s = survivalCurve(H, escapePct), seen = {}, out = [];
    [0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1].forEach(function (f) {
      var h = clamp(Math.round(H * f), 1, H);
      if (!seen[h]) { seen[h] = 1; out.push({ height: h, chance_pct: Math.round(s[h] * 10000) / 100, mult: multAt(s, h, edgePct) }); }
    });
    return out.sort(function (a, b) { return a.height - b.height; });
  }

  var DEMO_STYLES = ['bonk', 'yelp', 'cauldron', 'flick', 'grinder', 'boing', 'umbrella'];
  var DEMO_CAUSES = ['rock', 'hand', 'rope', 'chain', 'rib', 'bat', 'geyser', 'demon', 'tired'];
  var DEMO_PLAYERS = [['alice', 12, 200], ['bob', 30, 150], ['carol', 8, 120], ['dave', 30, 80], ['erin', 55, 25], ['frank', 14, 40]];

  // A short climb of about 15 levels with a couple of events and a random funny fall. Same script format as the server's.
  function demoScript(cfg, seed, max) {
    var H = clamp(Math.round(+cfg.max_height || 100), 10, 300), r = mulberry((seed | 0) || 7), M = max != null ? clamp(max, 0, H) : 11 + Math.floor(r() * 8);
    var beats = [], i;
    function add(ev, lv, to, d, extra) {
      var b = { t: 0, d: Math.round(d), ev: ev, lv: lv, to: to, s: Math.floor(r() * 2147483647) };
      if (extra) for (var k in extra) b[k] = extra[k];
      beats.push(b);
    }
    var kinds = ['chains', 'rope', 'ribs', 'chimney'], k1 = kinds[Math.floor(r() * 4)], k2 = kinds[(kinds.indexOf(k1) + 1 + Math.floor(r() * 3)) % 4];
    var cause = DEMO_CAUSES[Math.floor(r() * DEMO_CAUSES.length)], style = DEMO_STYLES[Math.floor(r() * DEMO_STYLES.length)];
    var want = { rope: 'rope', chain: 'chains', rib: 'ribs', rock: 'ledge' }[cause];
    var routes = [{ from: 0, to: 7, kind: 'ledge' }, { from: 7, to: 14, kind: k1 }, { from: 14, to: Math.min(H, 30), kind: want || k2 }];
    var ev = { 4: 'taunt', 9: r() < 0.5 ? 'geyser' : 'bat', 12: 'slip' }, flags = { 8: 1, 12: 1 }, a = 0, top = 0;
    add('ready', 0, 0, 1300);
    while (a < M) {
      if (ev[a] && M - a > 2) {
        var e = ev[a]; ev[a] = null;
        if (e === 'taunt') add('taunt', a, a, 2000, { kind: Math.floor(r() * 3), side: r() < 0.5 ? -1 : 1 });
        else if (e === 'geyser') add('geyser', a, a, 1500, { side: r() < 0.5 ? -1 : 1 });
        else if (e === 'bat') add('bat', a, a, 2100, { n: 3 });
        else if (e === 'slip') { add('slip', a, a - 2, 1250, { k: 2 }); a -= 2; }
        continue;
      }
      add('climb', a, a + 1, 330 + r() * 80);
      a++;
      if (a > top) { top = a; if (flags[a]) add('cheer', a, a, 700, { flags: [a] }); }
    }
    add('fatal', M, M, 1200, { cause: cause });
    var drop = Math.min(M, 60), bonks = Math.max(1, Math.min(8, 1 + Math.floor(M / 8)));
    var fd = { bonk: 600 + 430 * bonks + 1300, yelp: 1100 + drop * 40 + 1500, cauldron: 1000 + drop * 30 + 2600, flick: 1000 + drop * 30 + 2900,
      grinder: 1000 + drop * 26 + 3800, boing: 1000 + drop * 28 + 3400, umbrella: 1500 + drop * 65 + 2600 }[style];
    add('fall', M, 0, fd, style === 'bonk' ? { style: style, n: bonks } : { style: style });
    var t = 0;
    for (i = 0; i < beats.length; i++) { beats[i].t = t; t += beats[i].d; }
    return { v: 1, seed: (seed | 0) || 7, height: H, max: M, escaped: false, cause: cause, style: style, routes: routes, beats: beats, duration_ms: t };
  }

  function wholeState(game, idle) {
    return { state: game ? game.phase : 'idle', visible: true, spin: null, last: null, history: [], busy_ms: 0, announce: null, table: null,
      game: game, idle: idle || {}, preview: null };
  }

  // The sample game of the editor: bets open, six sample players, the rules box and the ladder.
  function sampleState(c, gid, ms) {
    var H = clamp(Math.round(+c.max_height || 100), 10, 300), esc_ = +c.escape_pct || 2, edge = +c.house_edge_pct || 0, s = survivalCurve(H, esc_), by = {}, players = {};
    DEMO_PLAYERS.forEach(function (p) {
      var h = clamp(Math.round(p[1] * Math.min(1, H / 100)), 1, H), m = multAt(s, h, edge);
      var mk = by[h] || (by[h] = { height: h, total: 0, users: [], mult: m, chance: Math.round(s[h] * 1e6) / 1e6 });
      mk.total += p[2]; mk.users.push({ user: p[0], amount: p[2] });
      var pl = players[p[0]] || (players[p[0]] = { user: p[0], stake: 0, bets: [] });
      pl.stake += p[2]; pl.bets.push({ height: h, amount: p[2], mult: m, pays: Math.floor(p[2] * m) });
    });
    var markers = Object.keys(by).map(Number).sort(function (a, b) { return a - b; }).map(function (h) { return by[h]; });
    var plist = Object.keys(players).map(function (k) { return players[k]; }).sort(function (a, b) { return b.stake - a.stake; });
    var cur = String(c.currency || 'coins'), odds = ladderOf(H, esc_, edge), name = String(c.soul_name || '').split(/[,;|]/)[0].trim() || 'Gary';
    return wholeState({
      id: gid, test: false, phase: 'betting', ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0, climb: 1, climbs: Math.max(1, Math.round(+c.climbs || 1)),
      height: H, soul: { name: name, seed: 11, skin: 1, gear: 2 }, edge_pct: edge, escape_pct: esc_, script: null, markers: markers, players: plist,
      at_risk: plist.reduce(function (a, p) { return a + p.stake; }, 0), odds: odds, mults: multsOf(H, esc_, edge), last: null, results: [], outcome: null, summary: null,
      currency: cur, min_bet: c.min_bet || 1, max_bet: c.max_bet || 0, max_bets: 5, commands_text: c.commands_text || ''
    }, { height: H, climbs: Math.max(1, Math.round(+c.climbs || 1)), soul: { name: name }, odds: odds, mults: multsOf(H, esc_, edge), currency: cur });
  }

  // ▶ Preview: the last seconds of the bet window -> the climb (a local script) -> the result card -> the game-over card.
  function demoSteps(c, gid) {
    var s0 = sampleState(c, gid, 3000), g0 = s0.game, seed = Math.floor(Math.random() * 2147483647) + 1, script = demoScript(c, seed);
    var M = script.max, win = [], lose = [], paid = {}, bet = 0, out = 0;
    g0.players.forEach(function (p) {
      p.bets.forEach(function (b) {
        bet += b.amount;
        if (b.height <= M) { win.push({ user: p.user, height: b.height, amount: b.amount, mult: b.mult, pays: b.pays }); paid[p.user] = (paid[p.user] || 0) + b.pays; out += b.pays; }
        else lose.push({ user: p.user, height: b.height, amount: b.amount });
      });
    });
    win.sort(function (a, b) { return b.pays - a.pays; }); lose.sort(function (a, b) { return b.amount - a.amount; });
    var name = g0.soul.name, res = { climb: 1, max: M, escaped: false, cause: script.cause, style: script.style, soul: name };
    var last = Object.assign({}, res, { winners: win, losers: lose });
    var summary = { outcome: 'complete', text: 'Every climb played', soul: g0.soul, climbs: 1, played: 1, results: [res], best: M, height: g0.height,
      total_bet: bet, total_paid: out, house_net: bet - out, test: false, currency: g0.currency,
      players: g0.players.map(function (p) { var pd = paid[p.user] || 0; return { user: p.user, bet: p.stake, paid: pd, net: pd - p.stake }; }).sort(function (a, b) { return b.net - a.net; }) };
    function step(phase, ms, extra) {
      var g = Object.assign({}, g0, { phase: phase, ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0 }, extra);
      return { ms: ms, state: wholeState(g, s0.idle) };
    }
    var resMs = clamp(+c.result_seconds || 8, 2, 30) * 1000;
    return [
      step('betting', 3000),
      step('climbing', script.duration_ms + TAIL_MS, { script: script, markers: g0.markers }),
      step('result', Math.min(resMs, 4000), { script: script, last: last, results: [res], players: [] }),
      step('over', 4000, { script: script, last: last, results: [res], players: [], outcome: 'complete', summary: summary })
    ];
  }

  // ==================================================================== depth: the far cave (through the windows) + the foreground
  var FAR = 0.34, NEAR = 1.5;

  // One baked piece of the far cave: a dim glow, great hex pillars, lava falls, rock islands, and - tiny, far away -
  // other damned souls on their own ropes and a demon or two. It slides by at a third of the wall's speed.
  function bakeFar(inst, idx) {
    var k = inst.k, th = inst.th, cv = mkCanvas(VW * k, CH * k), c = cv.getContext('2d'), rnd = mulberry(idx * 104729 + 5), i, j;
    c.setTransform(k, 0, 0, k, 0, 0);
    var warm = clamp(1 - idx / 9, 0, 1);
    var g = c.createLinearGradient(0, 0, 0, CH);
    g.addColorStop(0, mixc(th.sky1, th.lavaDeep, 0.55 + 0.2 * warm)); g.addColorStop(1, mixc(th.lavaDeep, th.lava0, 0.3 + 0.3 * warm));
    c.fillStyle = g; c.fillRect(0, 0, VW, CH);
    for (i = 0; i < 4; i++) {                              // a hazy glow from far below
      var hx = rnd() * VW, hy = rnd() * CH, hr = 90 + rnd() * 130, hg2 = c.createRadialGradient(hx, hy, 6, hx, hy, hr);
      hg2.addColorStop(0, 'rgba(' + th.glow + ',' + (0.38 + 0.2 * warm) + ')'); hg2.addColorStop(1, 'rgba(' + th.glow + ',0)');
      c.fillStyle = hg2; c.fillRect(hx - hr, hy - hr, hr * 2, hr * 2);
    }
    // the glow of lava falls
    for (i = 0; i < 3; i++) {
      var fx = rnd() * VW, fw = 8 + rnd() * 14, fg = c.createLinearGradient(0, 0, 0, CH);
      fg.addColorStop(0, 'rgba(' + th.glow + ',0)'); fg.addColorStop(0.5, 'rgba(' + th.glow + ',' + (0.45 + 0.2 * rnd()) + ')'); fg.addColorStop(1, 'rgba(' + th.glow + ',.15)');
      var hg = c.createLinearGradient(fx - 60, 0, fx + 60, 0); hg.addColorStop(0, 'rgba(' + th.glow + ',0)'); hg.addColorStop(0.5, 'rgba(' + th.glow + ',.16)'); hg.addColorStop(1, 'rgba(' + th.glow + ',0)');
      c.fillStyle = hg; c.fillRect(fx - 60, 0, 120, CH); c.fillStyle = fg; c.fillRect(fx - fw / 2, 0, fw, CH);
    }
    // hex pillars and rock islands, black against the glow
    for (i = 0; i < 6; i++) {
      var px = rnd() * VW, py = rnd() * CH, pr = 30 + rnd() * 70;
      c.fillStyle = mixc('#000000', th.sky0, 0.15 + rnd() * 0.25); c.strokeStyle = 'rgba(' + th.glow + ',.45)'; c.lineWidth = 2.4;
      if (rnd() < 0.5) { hexPath(c, px, py, pr); c.fill(); c.stroke(); hexPath(c, px, py, pr * 0.62); c.stroke(); }
      else { blob(c, px, py, pr, rnd, 1.6, 0.7); c.fill(); c.stroke(); for (j = 0; j < 4; j++) { c.fillStyle = 'rgba(' + th.glow + ',.85)'; c.fillRect(px - pr + rnd() * pr * 2, py - pr * 0.4 + rnd() * pr * 0.6, 3, 4); } }
    }
    // far away, other damned souls: climbing, hanging, waving, a demon with a pitchfork
    for (i = 0; i < 6; i++) {
      var sx = 20 + rnd() * (VW - 40), sy = 40 + rnd() * (CH - 80), kind = rnd(), sc = 0.8 + rnd() * 0.6;
      c.save(); c.translate(sx, sy); c.scale(sc, sc);
      c.strokeStyle = 'rgba(' + th.glow + ',.5)'; c.lineWidth = 1.6; c.beginPath(); c.moveTo(0, -40); c.lineTo(0, 40); c.stroke();     // its rope
      c.fillStyle = kind < 0.7 ? 'rgba(' + th.glow + ',.95)' : 'rgba(255,60,40,.95)'; c.strokeStyle = c.fillStyle; c.lineWidth = 2.4; c.lineCap = 'round';
      c.beginPath(); c.arc(0, -6, 4, 0, TAU); c.fill();
      c.beginPath(); c.moveTo(0, -2); c.lineTo(0, 8); c.moveTo(0, 8); c.lineTo(-4, 15); c.moveTo(0, 8); c.lineTo(4, 14); c.moveTo(0, 0); c.lineTo(-5, -10 + (rnd() < 0.5 ? 0 : 8)); c.moveTo(0, 0); c.lineTo(5, -9); c.stroke();
      if (kind > 0.7) { c.beginPath(); c.moveTo(9, 16); c.lineTo(9, -14); c.moveTo(5, -12); c.lineTo(9, -16); c.lineTo(13, -12); c.stroke(); }
      c.restore();
    }
    for (i = 0; i < 12; i++) { c.fillStyle = 'rgba(' + th.glow + ',' + (0.3 + rnd() * 0.5) + ')'; c.beginPath(); c.arc(rnd() * VW, rnd() * CH, 1 + rnd() * 2.2, 0, TAU); c.fill(); }
    // depth of field: the far cave is out of focus (the sharp copy underneath keeps the edges of the piece opaque)
    var cv2 = mkCanvas(cv.width, cv.height), c2 = cv2.getContext('2d');
    c2.drawImage(cv, 0, 0);
    try { c2.filter = 'blur(' + (3.4 * k).toFixed(1) + 'px)'; c2.drawImage(cv, 0, 0); c2.filter = 'none'; } catch (e) {}
    return cv2;
  }

  DRAW.far = function (I, c, sc, T) {
    var cam = I.cam, cf = cam.y * FAR, lo = Math.floor((cf - (VH - ANCHOR_Y) - 20) / CH), hi = Math.floor((cf + ANCHOR_Y + 20) / CH);
    for (var i = lo; i <= hi; i++) {
      var key = 'f:' + I.cfg.theme + ':' + i + ':' + I.k, cv = I.cache[key];
      if (!cv) { cv = bakeFar(I, i); I.cache[key] = cv; I.cacheList.push(key); if (I.cacheList.length > 40) delete I.cache[I.cacheList.shift()]; }
      c.drawImage(cv, 0, ANCHOR_Y - ((i + 1) * CH - cf) - 0.5, VW, CH + 1);
    }
  };

  // Rock claws and dangling things at the edges of the screen, in front of everything: they rush by faster than the wall.
  DRAW.foreground = function (I, c, sc, T) {
    var th = I.th, cam = I.cam, cf = cam.y * NEAR, step = 330, lo = Math.floor((cf - (VH - ANCHOR_Y) - 140) / step), hi = Math.ceil((cf + ANCHOR_Y + 140) / step);
    for (var i = lo; i <= hi; i++) {
      var side = hash(i * 13 + 1) < 0.5 ? -1 : 1, r = hash(i * 13 + 2), r2 = hash(i * 13 + 3), r3 = hash(i * 13 + 4);
      if (r3 < 0.25) continue;
      var wy = i * step + r * 200, y = ANCHOR_Y - (wy - cf), len = 46 + r2 * 40, x0 = side < 0 ? -8 : VW + 8, dir = side < 0 ? 1 : -1;
      var big = r2 > 0.55;
      c.save();
      // the claw: a jagged tongue of dark rock from the edge
      c.beginPath();
      c.moveTo(x0, y - 70 - r * 20);
      c.lineTo(x0 + dir * (len * 0.55), y - 36); c.lineTo(x0 + dir * (len * 0.4), y - 18); c.lineTo(x0 + dir * len, y + 8 + r2 * 10);
      c.lineTo(x0 + dir * (len * 0.5), y + 22); c.lineTo(x0 + dir * (len * 0.62), y + 46); c.lineTo(x0, y + 74 + r * 20);
      c.closePath();
      var g = c.createLinearGradient(x0, 0, x0 + dir * len, 0); g.addColorStop(0, '#05020a'); g.addColorStop(1, '#1b0d12');
      c.fillStyle = g; c.fill(); c.lineWidth = 3; c.strokeStyle = 'rgba(' + th.glow + ',.4)'; c.stroke();
      // a dangling chain or a bone from its tip
      var tx = x0 + dir * len, ty = y + 8 + r2 * 10;
      if (big) {
        for (var q = 0; q < 6; q++) link(c, tx - dir * 4 + Math.sin(T / 800 + i + q * 0.3) * (2 + q), ty + 8 + q * 12, 0, q % 2 ? 7 : 12, q % 2 ? 15 : 12, th, q % 2);
      } else {
        femur(c, tx - dir * 6, ty + 28, 40, th, Math.PI / 2 + Math.sin(T / 700 + i) * 0.1);
      }
      c.restore();
    }
  };

  // ------------------------------------------------------------------ registration
  function copyObj(o) { var r = {}; for (var k in o) if (has(o, k)) r[k] = o[k]; return r; }
  var API = {
    BASE_W: BASE_W, BASE_H: BASE_H, THEMES: THEMES, DEFAULTS: copyObj(DEFAULTS), APPEARANCE: APPEARANCE.slice(),
    create: function (container, config, opts) { return new CL(container, config, opts); },
    sampleState: sampleState, demoSteps: demoSteps, demoScript: demoScript, ladder: ladderOf, survival: survivalCurve,
    Timeline: Timeline, soulPose: soulPose, LV: LV, FOOT: FOOT, TAIL_MS: TAIL_MS
  };
  HG.climb = API;
  if (typeof module === 'object' && module && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
