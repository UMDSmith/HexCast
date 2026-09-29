/* Hexcast - shared top bar.
 *
 * Usage: put this in the page, as early in <body> as you like:
 *
 *   <div id="hexbar" data-section="Soundboard"></div>
 *   <div id="hexbar-actions"> ...page buttons... </div>
 *   <script src="/static/hexbar.js"></script>
 *
 * Anything inside #hexbar-actions is moved into the bar rather than recreated,
 * so existing event handlers bound by id keep working untouched.
 *
 * The tabs are not hard-wired: Soundboard is always first, then one tab per
 * installed plugin (GET /api/plugins/nav), then "+" (the plugin store) and Help.
 * A plugin's status dot is decided by a small script it ships (nav.status_js) that
 * registers window.HexbarStatus[key] = function (status) -> {on, warn, title}.
 */
(function () {
  var host = document.getElementById('hexbar');
  if (!host) return;

  var CACHE_KEY = 'hexcast.nav.v1';
  var FIRST = { key: 'soundboard', label: 'Soundboard', href: '/', color: 'var(--hb-accent)', alwaysOn: true };
  var LAST = [
    { key: 'store', label: '+', href: '/plugins', plus: true, nodot: true, title: 'Add plugins' },
    { key: 'help', label: 'Help', href: '/help', nodot: true }
  ];
  var items = [];          // the plugin tabs, from the server (or the cache until it answers)
  var updates = 0;         // installed plugins with a newer copy in the catalog (dot on the + tab)
  window.HexbarStatus = window.HexbarStatus || {};
  var loadedScripts = {};  // status_js url -> true

  var section = (host.dataset.section || '');
  var forcedKey = host.dataset.key || '';

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function allTabs() {
    return [FIRST].concat(items).concat(LAST);
  }

  // Accept either the key or the label, so data-section="Now playing" still
  // highlights Music if a page wants a friendlier name.
  function currentKey() {
    if (forcedKey) return forcedKey;
    var low = section.toLowerCase(), tabs = allTabs();
    for (var i = 0; i < tabs.length; i++) {
      if (tabs[i].key === low || String(tabs[i].label).toLowerCase() === low) return tabs[i].key;
    }
    return '';
  }

  function renderNav() {
    var cur = currentKey();
    var html = allTabs().map(function (t) {
      var cls = (t.key === cur ? 'sel' : '') + (t.plus ? ' hb-plus' + (updates ? ' has-update' : '') : '');
      var style = t.color ? ' style="--tab:' + esc(t.color) + '"' : '';
      var warnLink = (t.state && t.state !== 'running') ? ('/plugins#' + esc(t.id)) : t.href;
      return '<a href="' + esc(warnLink) + '" data-key="' + esc(t.key) + '"' +
             (cls.trim() ? ' class="' + cls.trim() + '"' : '') + style +
             (t.title ? ' title="' + esc(t.plus && updates ? updates + ' plugin update' + (updates === 1 ? '' : 's') + ' available - open + to update' : t.title) + '"' : '') + '>' +
             (t.nodot ? '' : '<i class="hb-dot" id="hb-dot-' + esc(t.key) + '"></i>') + esc(t.label) + '</a>';
    }).join('');
    var nav = host.querySelector('.hb-nav');
    if (nav) nav.innerHTML = html;
    // a re-render forgets the dots: put them back at once
    var sb = document.getElementById('hb-dot-soundboard');
    if (sb) { sb.classList.add('on'); sb.parentNode.title = 'Soundboard'; }
    items.forEach(function (t) {
      if (t.state && t.state !== 'running') {
        setDot(t.key, false, true, t.label + ' is not running - ' +
          (t.error || (t.state === 'needs_deps' ? 'its Python packages are missing' : t.state)));
      }
    });
    lastResults && Object.keys(lastResults).forEach(function (k) {
      var r = lastResults[k];
      setDot(k, r.on, r.warn, r.title);
    });
  }

  host.innerHTML =
    '<a class="hb-brand" href="/" title="Hexcast">' +
      '<img src="/static/hexcast.png" alt="Hexcast">' +
      '<span class="hb-word">Hex<b>cast</b></span>' +
    '</a>' +
    (section ? '<span class="hb-sep">/</span><span class="hb-section">' + esc(section) + '</span>' : '') +
    '<nav class="hb-nav"></nav>' +
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

  var lastResults = {};    // key -> last {on, warn, title}, kept across re-renders

  function setDot(key, on, warn, title) {
    var dot = document.getElementById('hb-dot-' + key);
    if (!dot) return;
    dot.classList.toggle('on', !!on);
    dot.classList.toggle('warn', !!warn && !on);
    if (title) dot.parentNode.title = title;
  }

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

  // ---- the plugin tabs ---------------------------------------------------------------

  var lastSig = '';
  function applyItems(list, fromCache) {
    var sig = JSON.stringify(list);
    if (sig === lastSig) return;
    lastSig = sig;
    items = list.map(function (i) {
      return { id: i.id, key: i.key || i.id, label: i.label, href: i.href, color: i.color,
               state: i.state, error: i.error, status_url: i.status_url, status_js: i.status_js };
    });
    renderNav();
    if (!fromCache) {
      try { localStorage.setItem(CACHE_KEY, JSON.stringify(list)); } catch (e) {}
    }
    items.forEach(loadAdapter);
    pollStatus();
  }

  function loadAdapter(t) {
    if (!t.status_js || loadedScripts[t.status_js]) return;
    loadedScripts[t.status_js] = true;
    var s = document.createElement('script');
    s.src = t.status_js;
    s.onload = pollStatus;
    document.head.appendChild(s);
  }

  function fetchNav() {
    return fetch('/api/plugins/nav', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !Array.isArray(d.items)) return;
        var n = Array.isArray(d.updates) ? d.updates.length : 0;
        if (n !== updates) { updates = n; renderNav(); }
        applyItems(d.items, false);
      })
      .catch(function () {});
  }

  // ---- status dots -------------------------------------------------------------------

  function pollOne(t) {
    if (!t.status_url || (t.state && t.state !== 'running')) return;
    var adapter = window.HexbarStatus[t.key];
    fetch(t.status_url, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (s) {
        var res;
        try {
          res = typeof adapter === 'function' ? adapter(s)
            : { on: !!s, warn: !s, title: t.label + (s ? '' : ' - not responding') };
        } catch (e) {
          res = { on: false, warn: true, title: t.label + ' - status error' };
        }
        res = res || {};
        lastResults[t.key] = res;
        setDot(t.key, res.on, res.warn, res.title);
      });
  }

  function pollStatus() { items.forEach(pollOne); }

  // ---- boot ----------------------------------------------------------------------------

  var cached = null;
  try { cached = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null'); } catch (e) {}
  if (Array.isArray(cached)) {
    // paint the last known tabs at once (no jump), then let the server correct them
    items = cached.map(function (i) {
      return { id: i.id, key: i.key || i.id, label: i.label, href: i.href, color: i.color,
               state: i.state, error: i.error, status_url: i.status_url, status_js: i.status_js };
    });
    lastSig = JSON.stringify(cached);
  }
  renderNav();
  items.forEach(loadAdapter);
  fetchNav().then(pollStatus);
  setInterval(function () { fetchNav().then(pollStatus); }, 10000);
  window.HexbarRefresh = function () { return fetchNav().then(pollStatus); };   // e.g. right after an install
})();
