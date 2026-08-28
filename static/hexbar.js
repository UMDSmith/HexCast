/* Hexcast — shared top bar.
 *
 * Usage: put this in the page, as early in <body> as you like:
 *
 *   <div id="hexbar" data-section="Soundboard"></div>
 *   <div id="hexbar-actions"> ...page buttons... </div>
 *   <script src="/static/hexbar.js"></script>
 *
 * Anything inside #hexbar-actions is moved into the bar rather than recreated,
 * so existing event handlers bound by id keep working untouched.
 */
(function () {
  var SECTIONS = [
    { key: 'soundboard', label: 'Soundboard', href: '/' },
    { key: 'twitch',     label: 'Twitch',     href: '/twitch', status: '/twitch/api/status' },
    { key: 'music',      label: 'Music',      href: '/ytm',    status: '/ytm/api/status' },
    { key: 'discord',    label: 'Discord',    href: '/discord', status: '/discord/api/status' },
    { key: 'clips',      label: 'Clips',      href: '/clips',   status: '/clips/api/status' },
    { key: 'countdown',  label: 'Countdown',  href: '/countdown', status: '/countdown/api/status' },
    { key: 'help',       label: 'Help',       href: '/help',    nodot: true }
  ];

  var host = document.getElementById('hexbar');
  if (!host) return;

  var current = (host.dataset.section || '').toLowerCase();
  // Accept either the key or the label, so data-section="Now playing" still
  // highlights Music if a page wants a friendlier name.
  var currentKey = host.dataset.key ||
    (SECTIONS.filter(function (s) { return s.key === current || s.label.toLowerCase() === current; })[0] || {}).key ||
    '';

  var nav = SECTIONS.map(function (s) {
    return '<a href="' + s.href + '" data-key="' + s.key + '"' +
           (s.key === currentKey ? ' class="sel"' : '') + '>' +
           (s.nodot ? '' : '<i class="hb-dot" id="hb-dot-' + s.key + '"></i>') + s.label + '</a>';
  }).join('');

  host.innerHTML =
    '<a class="hb-brand" href="/" title="Hexcast">' +
      '<img src="/static/hexcast.png" alt="Hexcast">' +
      '<span class="hb-word">Hex<b>cast</b></span>' +
    '</a>' +
    (host.dataset.section
      ? '<span class="hb-sep">/</span><span class="hb-section">' + host.dataset.section + '</span>'
      : '') +
    '<nav class="hb-nav">' + nav + '</nav>' +
    '<span class="hb-spacer"></span>' +
    '<a class="hb-ver" id="hb-ver" href="https://github.com/UMDSmith/hexcast" target="_blank" rel="noopener"></a>' +
    '<div class="hb-actions" id="hb-actions"></div>';

  // Relocate the page's own buttons into the bar, keeping their handlers.
  var actions = document.getElementById('hexbar-actions');
  if (actions) {
    var target = document.getElementById('hb-actions');
    while (actions.firstChild) target.appendChild(actions.firstChild);
    actions.parentNode.removeChild(actions);
  }

  // The soundboard is whatever is serving this page, so it is always up.
  var sbDot = document.getElementById('hb-dot-soundboard');
  if (sbDot) { sbDot.classList.add('on'); sbDot.parentNode.title = 'Soundboard'; }

  // Version chip: show the running version, and flag when a newer one exists.
  // The server does the (cached) GitHub check, so this is one cheap local call.
  var verEl = document.getElementById('hb-ver');
  if (verEl) {
    fetch('/api/version')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (v) {
        if (!v) { verEl.style.display = 'none'; return; }
        verEl.textContent = 'v' + v.version;
        if (v.update_available) {
          verEl.textContent = 'v' + v.version + ' • update';
          verEl.classList.add('has-update');
          verEl.title = 'Update available: v' + v.latest + ' — click to open the Hexcast repo';
        } else {
          verEl.title = 'Hexcast v' + v.version + (v.latest ? ' (up to date)' : '');
        }
      })
      .catch(function () { verEl.style.display = 'none'; });
  }

  function setDot(key, on, warn, title) {
    var dot = document.getElementById('hb-dot-' + key);
    if (!dot) return;
    dot.classList.toggle('on', !!on);
    dot.classList.toggle('warn', !!warn && !on);
    dot.parentNode.title = title;
  }

  function poll() {
    fetch('/twitch/api/status')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        var live = !!(s && s.connected);
        setDot('twitch', live, !!(s && !live), live
          ? (s.source === 'eventsub'
              ? 'Twitch connected — chat and events'
              : 'Twitch connected — chat only, not signed in')
          : 'Twitch not connected');
      })
      .catch(function () { setDot('twitch', false, false, 'Twitch module not installed'); });

    fetch('/ytm/api/status')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        var live = !!(s && s.connected);
        var title;
        if (live && s.now && s.now.title) {
          title = (s.now.playing ? '\u25B6 ' : '\u23F8 ') + s.now.title + ' — ' + s.now.author;
        } else if (live) {
          title = 'YouTube Music connected — nothing playing';
        } else if (s && s.paired) {
          title = 'YouTube Music Desktop is not running';
        } else {
          title = 'YouTube Music not paired';
        }
        setDot('music', live, !!(s && s.paired && !live), title);
      })
      .catch(function () { setDot('music', false, false, 'Music module not installed'); });

    fetch('/discord/api/status')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        var live = !!(s && s.connected);
        var title;
        if (live && s.channel) {
          title = 'Discord connected — ' + s.channel.name;
        } else if (live) {
          title = 'Discord connected — not in a voice channel';
        } else if (s && s.authed) {
          title = 'Discord desktop client is not running';
        } else {
          title = 'Discord not authorized';
        }
        setDot('discord', live, !!(s && s.authed && !live), title);
      })
      .catch(function () { setDot('discord', false, false, 'Discord module not installed'); });

    fetch('/clips/api/status')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s) { setDot('clips', false, false, 'Clips module not installed'); return; }
        var live = !!s.ytdlp;
        var title;
        if (s.player && s.player.state !== 'idle' && s.player.item) {
          title = 'Clips — playing #' + s.player.item.num + ' ' + (s.player.item.title || '');
        } else if (live) {
          title = 'Clips — ' + s.queued + ' queued';
        } else {
          title = 'Clips — yt-dlp not installed';
        }
        setDot('clips', live, !live, title);
      })
      .catch(function () { setDot('clips', false, false, 'Clips module not installed'); });
  }

  poll();
  setInterval(poll, 10000);
})();
