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
    renderVersions();
    // a re-render forgets the dots: put them back at once
    var sb = document.getElementById('hb-dot-soundboard');
    if (sb) { sb.classList.add('on'); sb.parentNode.title = 'Soundboard'; }
    items.forEach(function (t) {
      if (t.state && t.state !== 'running') {
        delete lastResults[t.key];               // an old green dot must not come back over the warning
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
    '<div class="hb-vers" id="hb-vers"></div>' +
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

  // ---- versions: "<Module> Version: 1.0" for this tab's plugin, "Hexcast Version: 2.0" for the core ----
  // Both read local data only: /api/plugins/nav (the module; the server compares it with the catalog and
  // with GitHub in the background) and /api/version (the core). 1.0.0 is shown as 1.0.
  var versEl = document.getElementById('hb-vers');
  var core = null;            // /api/version
  var modMsg = {};            // plugin id -> {busy, text} while / after an update from this bar

  function fmtVer(v) {
    var m = /^(\d+)\.(\d+)\.0$/.exec(String(v == null ? '' : v));
    return m ? m[1] + '.' + m[2] : String(v == null ? '' : v);
  }

  function currentItem() {
    var cur = currentKey();
    if (!cur || cur === 'soundboard' || cur === 'store' || cur === 'help') return null;
    for (var i = 0; i < items.length; i++) if (items[i].key === cur) return items[i];
    return null;
  }

  function chip(cls, name, ver, extra, title) {
    return '<span class="hb-v ' + cls + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' +
      '<span class="hb-vn">' + esc(name) + '</span><span class="hb-vw"> Version:</span> <b>' + esc(ver) + '</b>' + (extra || '') + '</span>';
  }

  function renderVersions() {
    if (!versEl) return;
    var out = '';
    var m = currentItem();
    if (m && m.version_label) {
      var msg = modMsg[m.id], extra = '';
      if (msg && msg.busy) extra = '<span class="hb-up busy"><i class="hb-spin"></i>Updating</span>';
      else if (msg && msg.text) extra = '<span class="hb-up err" title="' + esc(msg.text) + '">Update failed</span>';
      else if (m.update_available) {
        extra = '<button type="button" class="hb-up" data-up="' + esc(m.id) + '" title="Update ' + esc(m.label) + ' to ' +
          esc(fmtVer(m.latest_version)) + (m.update_source === 'upstream' ? ' (downloaded from GitHub)' : '') + '">Update to ' + esc(m.latest_label || fmtVer(m.latest_version)) + '</button>';
      }
      out += chip('hb-mod', m.label, m.version_label, extra, m.label + ' module version ' + m.version_label);
    }
    if (core) {
      var cx = '';
      if (core.update_available && core.latest) {
        cx = '<a class="hb-up" href="' + esc(core.download_url || core.repo_url || 'https://github.com/UMDSmith/hexcast') +
          '" target="_blank" rel="noopener" title="Download the new Hexcast (a ZIP from GitHub) and unpack it over this folder - your settings stay">Update to ' +
          esc(fmtVer(core.latest)) + '</a>';
      }
      out += chip('hb-core', 'Hexcast', fmtVer(core.version), cx, 'Hexcast ' + fmtVer(core.version) +
        (core.update_available ? ' - version ' + fmtVer(core.latest) + ' is available' : core.latest ? ' (up to date)' : ''));
    }
    if (versEl.innerHTML !== out) versEl.innerHTML = out;
  }

  // Update ONE module through the same staged path as the + store (new files and packages are prepared
  // while it keeps running; the old copy stays if anything fails). Resolves {ok, error}.
  window.HexbarUpdate = function (pid, onLine) {
    function j(r) { return r.json().catch(function () { return {}; }); }
    return fetch('/api/plugins/' + encodeURIComponent(pid) + '/update', { method: 'POST', cache: 'no-store' })
      .then(j).then(function (d) {
        if (!d.ok && !d.job) return { ok: false, error: d.error || 'The update did not start.' };
        return new Promise(function (resolve) {
          var seen = 0;
          (function tick() {
            fetch('/api/plugins/jobs/' + encodeURIComponent(d.job) + '?since=' + seen, { cache: 'no-store' }).then(j).then(function (x) {
              if (!x || !x.ok) { resolve({ ok: false, error: 'Lost track of the update.' }); return; }
              seen = x.total;
              if (onLine && x.lines) x.lines.forEach(onLine);
              if (x.state === 'running') { setTimeout(tick, 600); return; }
              resolve({ ok: x.state === 'done', error: x.error || '' });
            }).catch(function () { setTimeout(tick, 1500); });
          })();
        });
      })
      .catch(function () { return { ok: false, error: 'Could not reach Hexcast.' }; });
  };

  if (versEl) {
    versEl.addEventListener('click', function (e) {
      var b = e.target.closest('[data-up]');
      if (!b) return;
      var pid = b.getAttribute('data-up');
      modMsg[pid] = { busy: true };
      renderVersions();
      window.HexbarUpdate(pid).then(function (r) {
        if (r.ok) { modMsg[pid] = { busy: true }; renderVersions(); setTimeout(function () { location.reload(); }, 500); return; }
        modMsg[pid] = { text: r.error || 'The update failed.' };
        renderVersions();
        setTimeout(function () { delete modMsg[pid]; renderVersions(); }, 8000);
      });
    });
  }

  function loadCore() {
    return fetch('/api/version', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (v) {
        if (v) { core = v; renderVersions(); }
        // the server asks GitHub in the background: look again soon, then now and then
        setTimeout(loadCore, v && v.checking ? 5000 : 10 * 60 * 1000);
      })
      .catch(function () { setTimeout(loadCore, 60000); });
  }
  loadCore();

  // ---- the plugin tabs ---------------------------------------------------------------

  var lastSig = '';
  function applyItems(list, fromCache) {
    var sig = JSON.stringify(list);
    if (sig === lastSig) return;
    lastSig = sig;
    items = list.map(function (i) {
      return { id: i.id, key: i.key || i.id, label: i.label, href: i.href, color: i.color,
               state: i.state, error: i.error, status_url: i.status_url, status_js: i.status_js,
               version_label: i.version_label, latest_version: i.latest_version, latest_label: i.latest_label,
               update_available: !!i.update_available, update_source: i.update_source };
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
               state: i.state, error: i.error, status_url: i.status_url, status_js: i.status_js,
               version_label: i.version_label, latest_version: i.latest_version, latest_label: i.latest_label,
               update_available: !!i.update_available, update_source: i.update_source };
    });
    lastSig = JSON.stringify(cached);
  }
  renderNav();
  items.forEach(loadAdapter);
  fetchNav().then(pollStatus);
  setInterval(function () { fetchNav().then(pollStatus); }, 10000);
  window.HexbarRefresh = function () { return fetchNav().then(pollStatus); };   // e.g. right after an install
})();
