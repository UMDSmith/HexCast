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
    { key: 'music',      label: 'Music',      href: '/ytm',    status: '/ytm/api/status' }
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
           '<i class="hb-dot" id="hb-dot-' + s.key + '"></i>' + s.label + '</a>';
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
  }

  poll();
  setInterval(poll, 10000);
})();
