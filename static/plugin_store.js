/* Hexcast - the plugin store widget.
 *
 * One widget, used twice: the "+" page (top-level plugins) and the "+" inside the Games
 * tab (the games, which are add-ons of the Games plugin).
 *
 *   HexStore.mount(element, {
 *     parent: null,            // null = top-level plugins; 'games' = that plugin's add-ons
 *     noun: 'plugin',          // wording: 'plugin' | 'game'
 *     onChange: function (what, id) {},   // what: 'installed' | 'removed' | 'updated' | 'enabled' | 'disabled'
 *   });
 *
 * It only talks to /api/plugins (see docs/plugins.md).
 */
(function () {
  'use strict';

  var CSS = [
    '.hs{ --hs-line:var(--hb-line,#232330); --hs-card:var(--hb-card,#17171f); --hs-ink:var(--hb-ink,#e8e8ef);',
    '  --hs-dim:var(--hb-dim,#8a8a9c); --hs-muted:var(--hb-muted,#a0a0b0); --hs-good:var(--hb-good,#3ddc84);',
    '  --hs-warn:var(--hb-warn,#ffb020); --hs-bad:#ff6b62; font-family:"Segoe UI",system-ui,sans-serif; color:var(--hs-ink); }',
    '.hs h3{ margin:22px 0 10px; font:800 12px/1 var(--hb-mono,monospace); letter-spacing:.18em; text-transform:uppercase; color:var(--hs-dim); }',
    '.hs h3 small{ font-weight:600; letter-spacing:.05em; margin-left:8px; }',
    '.hs-grid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:14px; }',
    '.hs-card{ background:var(--hs-card); border:1px solid var(--hs-line); border-top:3px solid var(--c,#35354a); border-radius:12px;',
    '  padding:16px; display:flex; flex-direction:column; gap:10px; min-width:0; scroll-margin-top:16px; }',
    '.hs-card.flash{ box-shadow:0 0 0 2px var(--hs-good); }',
    '.hs-head{ display:flex; align-items:center; gap:12px; }',
    '.hs-icon{ width:40px; height:40px; border-radius:10px; display:grid; place-items:center; font-size:22px; flex:0 0 auto;',
    '  background:color-mix(in srgb, var(--c,#35354a) 22%, transparent); }',
    '.hs-title{ flex:1; min-width:0; }',
    '.hs-name{ font-size:16px; font-weight:700; }',
    '.hs-ver{ font:600 11px/1 var(--hb-mono,monospace); color:var(--hs-dim); margin-left:8px; }',
    '.hs-desc{ color:var(--hs-muted); font-size:13px; line-height:1.5; }',
    '.hs-badge{ font:700 10px/1 var(--hb-mono,monospace); letter-spacing:.08em; text-transform:uppercase; padding:4px 8px;',
    '  border-radius:99px; border:1px solid var(--hs-line); color:var(--hs-dim); white-space:nowrap; }',
    '.hs-badge.good{ color:var(--hs-good); border-color:#1d4d33; background:#0e2118; }',
    '.hs-badge.warn{ color:var(--hs-warn); border-color:#5a4210; background:#231a08; }',
    '.hs-badge.bad{ color:var(--hs-bad); border-color:#5a2420; background:#241010; }',
    '.hs-chips{ display:flex; flex-wrap:wrap; gap:6px; }',
    '.hs-chip{ font-size:11px; color:var(--hs-dim); border:1px solid var(--hs-line); border-radius:99px; padding:3px 9px; }',
    '.hs-err{ font-size:12.5px; color:var(--hs-bad); background:#241010; border:1px solid #5a2420; border-radius:8px; padding:8px 10px; overflow-wrap:anywhere; }',
    '.hs-note{ font-size:12.5px; color:var(--hs-warn); background:#231a08; border:1px solid #5a4210; border-radius:8px; padding:8px 10px; }',
    '.hs-actions{ display:flex; flex-wrap:wrap; gap:8px; margin-top:auto; padding-top:4px; }',
    '.hs-btn{ font:600 13px/1 "Segoe UI",system-ui,sans-serif; padding:9px 14px; border-radius:8px; cursor:pointer; text-decoration:none;',
    '  border:1px solid var(--hs-line); background:var(--hb-panel,#131319); color:var(--hs-ink); display:inline-block; }',
    '.hs-btn:hover{ border-color:var(--hb-line-hi,#35354a); }',
    '.hs-btn:disabled{ opacity:.5; cursor:default; }',
    '.hs-btn.primary{ background:var(--hs-good); border-color:var(--hs-good); color:#06110a; }',
    '.hs-btn.primary:hover{ filter:brightness(1.08); }',
    '.hs-btn.danger:hover{ border-color:var(--hs-bad); color:var(--hs-bad); }',
    '.hs-btn.link{ border-color:transparent; background:transparent; color:var(--hs-dim); }',
    '.hs-log{ margin:0; max-height:170px; overflow:auto; background:#08080c; border:1px solid var(--hs-line); border-radius:8px; padding:9px 10px;',
    '  font:11.5px/1.5 var(--hb-mono,monospace); color:var(--hs-muted); white-space:pre-wrap; overflow-wrap:anywhere; }',
    '.hs-spin{ display:inline-block; width:12px; height:12px; border:2px solid var(--hs-line); border-top-color:var(--hs-good);',
    '  border-radius:50%; animation:hs-rot .8s linear infinite; vertical-align:-2px; margin-right:7px; }',
    '@keyframes hs-rot{ to{ transform:rotate(360deg); } }',
    '.hs-empty{ color:var(--hs-dim); font-size:14px; padding:22px; border:1px dashed var(--hs-line); border-radius:12px; text-align:center; }',
    '.hs-confirm{ font-size:13px; color:var(--hs-ink); }'
  ].join('\n');

  function injectCss() {
    if (document.getElementById('hs-css')) return;
    var st = document.createElement('style');
    st.id = 'hs-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function api(method, url, body) {
    var opt = { method: method, cache: 'no-store', headers: {} };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    return fetch(url, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { d.__status = r.status; return d; });
    });
  }

  function mount(el, opts) {
    injectCss();
    opts = opts || {};
    var parent = opts.parent || null;
    var noun = opts.noun || 'plugin';
    var Noun = noun.charAt(0).toUpperCase() + noun.slice(1);
    var st = {
      plugins: [], revision: -1, loaded: false, error: '',
      job: null,            // {id, plugin, title, lines, state, error}
      ask: null,            // {id, kind: 'remove'|'disable'|'cascade', names: []}
      note: {},             // plugin id -> message under the card
      notice: ''            // one message above the lists (where a removed plugin's files went ...)
    };
    el.classList.add('hs');

    function byId(id) {
      for (var i = 0; i < st.plugins.length; i++) if (st.plugins[i].id === id) return st.plugins[i];
      return null;
    }
    function nameOf(id) { var p = byId(id); return p ? p.name : id; }

    // ---- loading ----------------------------------------------------------------------
    function load(force) {
      var q = '/api/plugins' + (parent ? '?parent=' + encodeURIComponent(parent) : '');
      return api('GET', q).then(function (d) {
        if (!d || !d.ok) { st.error = 'Could not read the plugin list.'; st.loaded = true; render(); return; }
        st.error = '';
        var changed = force || d.revision !== st.revision || !st.loaded;
        st.revision = d.revision;
        st.plugins = d.plugins || [];
        st.catalogErrors = d.catalog_errors || {};
        st.loaded = true;
        if (d.busy && !st.job) followJob(d.busy.id, d.busy.plugin, d.busy.title);
        if (changed) render();
      }).catch(function () { st.error = 'Could not reach Hexcast.'; st.loaded = true; render(); });
    }

    // ---- jobs ---------------------------------------------------------------------------
    function followJob(id, plugin, title) {
      st.job = { id: id, plugin: plugin, title: title || '', lines: [], state: 'running', error: '' };
      render();
      var seen = 0;
      (function tick() {
        api('GET', '/api/plugins/jobs/' + encodeURIComponent(id) + '?since=' + seen).then(function (d) {
          if (!d || !d.ok) { st.job = null; load(true); return; }
          if (d.lines && d.lines.length) st.job.lines = st.job.lines.concat(d.lines);
          seen = d.total;
          st.job.state = d.state; st.job.error = d.error || '';
          render();
          if (d.state === 'running') { setTimeout(tick, 650); return; }
          var done = st.job;
          load(true).then(function () {
            refreshBar();
            if (done.state === 'done' && opts.onChange) {
              var kind = /^Update/.test(done.title) ? 'updated' : /^Install/.test(done.title) ? 'installed' : 'repaired';
              opts.onChange(kind, done.plugin);
            }
            st.note = {};                                          // ("another install is running" ... is over)
            if (done.state === 'done') { setTimeout(function () { if (st.job === done) { st.job = null; render(); } }, 2500); }
            else render();                                        // an error keeps its log until dismissed, buttons back
          });
        }).catch(function () { setTimeout(tick, 1500); });
      })();
    }

    function startJob(pid, action) {
      st.note[pid] = '';
      api('POST', '/api/plugins/' + encodeURIComponent(pid) + '/' + action).then(function (d) {
        if (d.ok && d.job) { followJob(d.job, pid, action.charAt(0).toUpperCase() + action.slice(1) + ' ' + nameOf(pid)); return; }
        if (d.job) { followJob(d.job, d.plugin || pid, d.title || ''); }   // another one is running: watch that
        st.note[pid] = d.error || 'That did not work.';
        render();
      });
    }

    function simple(pid, action, body, what) {
      api('POST', '/api/plugins/' + encodeURIComponent(pid) + '/' + action, body).then(function (d) {
        if (d.__status === 409 && d.dependents) {
          st.ask = { id: pid, kind: 'cascade', names: d.names || d.dependents, action: action };
          render(); return;
        }
        if (!d.ok) { st.note[pid] = d.error || 'That did not work.'; render(); return; }
        st.ask = null;
        var kept = d.kept ? Object.keys(d.kept) : [];
        st.notice = kept.length ? kept.map(function (k) { return nameOf(k) + ' was added by hand, so a copy of its files was kept in ' + d.kept[k]; }).join('. ') + '.' : '';
        if (d.restart_recommended) st.note[pid] = 'Stopped, but it did not shut down cleanly - restart Hexcast to be sure.';
        if (action === 'enable' && d.running === false && d.error) st.note[pid] = 'Could not start: ' + d.error;
        load(true).then(function () { refreshBar(); if (opts.onChange) opts.onChange(what, pid); });
      });
    }

    function refreshBar() { if (window.HexbarRefresh) window.HexbarRefresh(); }   // new tab / dot right away

    // ---- rendering ------------------------------------------------------------------------
    function badge(p) {
      if (!p.installed) return '';
      if (p.state === 'running') return '<span class="hs-badge good">Running</span>';
      if (p.state === 'disabled') return '<span class="hs-badge">Off</span>';
      if (p.state === 'needs_deps') return '<span class="hs-badge warn">Needs packages</span>';
      if (p.state === 'error') return '<span class="hs-badge bad">Problem</span>';
      return '<span class="hs-badge warn">Stopped</span>';
    }

    function chips(p) {
      var out = [];
      if (p.hidden) out.push('Library');
      var need = (p.requires_info || []).filter(function (r) { return r.id !== p.parent && !r.installed; });
      if (need.length && !p.installed) out.push('Also installs: ' + need.map(function (r) { return r.name; }).join(', '));
      (p.recommends_info || []).forEach(function (r) {
        if (!r.installed) out.push('Works better with: ' + r.name);
      });
      if (p.needs_packages && !p.installed) out.push('Downloads Python packages');
      if (p.source && p.source !== 'bundled' && p.source !== 'local') out.push('From ' + p.source.replace(/^https?:\/\//, '').split('/')[0]);
      if (p.source === 'local') out.push('Added by hand');
      return out.map(function (c) { return '<span class="hs-chip">' + esc(c) + '</span>'; }).join('');
    }

    function actions(p) {
      var busy = !!(st.job && st.job.state === 'running');
      var dis = busy ? ' disabled' : '';
      var a = st.ask && st.ask.id === p.id ? st.ask : null;
      if (a) {
        var msg;
        if (a.kind === 'cascade') {
          msg = (a.action === 'disable' ? 'Turning this off also stops: ' : 'Removing this also removes: ') + esc(a.names.join(', ')) + '.';
        } else if (a.kind === 'remove') {
          var also = (p.dependents_info || []).map(function (d) { return d.name; });
          msg = 'Remove ' + esc(p.name) + '?' + (also.length ? ' This also removes: ' + esc(also.join(', ')) + '.' : '') +
            ' Your settings are kept.' + (!p.source || p.source === 'local'
              ? ' It was added by hand, so a copy of its files is kept in plugins/.removed/.' : '');
        } else {
          msg = 'Turn off ' + esc(p.name) + '? It also stops: ' + esc((p.dependents_info || []).map(function (d) { return d.name; }).join(', ')) + '.';
        }
        var act = a.kind === 'remove' || (a.kind === 'cascade' && a.action !== 'disable') ? 'uninstall' : 'disable';
        return '<span class="hs-confirm">' + msg + '</span>' +
          '<div class="hs-actions"><button class="hs-btn danger" data-do="confirm" data-id="' + esc(p.id) + '" data-act="' + act +
          '">' + (act === 'uninstall' ? 'Remove' : 'Turn off') + '</button>' +
          '<button class="hs-btn link" data-do="cancel">Cancel</button></div>';
      }
      var b = [];
      if (!p.installed) {
        b.push('<button class="hs-btn primary" data-do="install" data-id="' + esc(p.id) + '"' + dis + '>Install</button>');
      } else {
        if (p.state === 'needs_deps') b.push('<button class="hs-btn primary" data-do="repair" data-id="' + esc(p.id) + '"' + dis + '>Repair</button>');
        if (p.state === 'error') b.push('<button class="hs-btn primary" data-do="enable" data-id="' + esc(p.id) + '"' + dis + '>Try again</button>');
        if (p.state === 'error' && p.in_catalog && p.source !== 'local') {
          b.push('<button class="hs-btn" data-do="update" data-id="' + esc(p.id) + '"' + dis + ' title="Put back a fresh copy of the plugin\'s files">Reinstall</button>');
        }
        if (p.state === 'running' && p.nav && p.nav.href && !p.parent) {
          b.push('<a class="hs-btn primary" href="' + esc(p.nav.href) + '">Open</a>');
        }
        if (p.update_available) {
          b.push('<button class="hs-btn' + (p.state === 'running' ? '' : ' primary') + '" data-do="update" data-id="' + esc(p.id) + '"' + dis +
                 '>Update' + (p.latest_version && p.latest_version !== p.installed_version ? ' to ' + esc(p.latest_version) : '') + '</button>');
        }
        if (p.state === 'disabled' || p.state === 'stopped') b.push('<button class="hs-btn" data-do="enable" data-id="' + esc(p.id) + '"' + dis + '>Turn on</button>');
        if (p.state === 'running' && !p.hidden) b.push('<button class="hs-btn" data-do="disable" data-id="' + esc(p.id) + '"' + dis + '>Turn off</button>');
        b.push('<button class="hs-btn danger" data-do="remove" data-id="' + esc(p.id) + '"' + dis + '>Remove</button>');
      }
      return '<div class="hs-actions">' + b.join('') + '</div>';
    }

    function card(p) {
      var c = p.color || '#35354a';
      var job = st.job && st.job.plugin && st.job.plugin.split(',').indexOf(p.id) >= 0 ? st.job : null;
      var html = '<div class="hs-card" id="hs-' + esc(p.id) + '" style="--c:' + esc(c) + '">' +
        '<div class="hs-head"><div class="hs-icon">' + esc(p.icon || '🧩') + '</div>' +
        '<div class="hs-title"><span class="hs-name">' + esc(p.name) + '</span>' +
        '<span class="hs-ver">' + esc(p.installed ? (p.installed_version || p.version) : p.version) + '</span></div>' +
        badge(p) + '</div>' +
        '<div class="hs-desc">' + esc(p.description) + '</div>';
      var ch = chips(p);
      if (ch) html += '<div class="hs-chips">' + ch + '</div>';
      if (p.installed && p.error && p.state !== 'running') html += '<div class="hs-err">' + esc(p.error) + '</div>';
      if (st.note[p.id]) html += '<div class="hs-note">' + esc(st.note[p.id]) + '</div>';
      if (job) {
        html += '<div class="hs-desc">' + (job.state === 'running' ? '<span class="hs-spin"></span>' : job.state === 'done' ? '✔ ' : '✖ ') +
          esc(job.state === 'running' ? (job.title || 'Working') + ' ...' : job.state === 'done' ? 'Done' : (job.error || 'Failed')) + '</div>' +
          '<pre class="hs-log" id="hs-log-' + esc(p.id) + '">' + esc(job.lines.join('\n')) + '</pre>';
        if (job.state !== 'running') {
          html += actions(p);
          if (job.state === 'error') html += '<div class="hs-actions"><button class="hs-btn link" data-do="dismiss">Hide this</button></div>';
        }
      } else {
        html += actions(p);
      }
      return html + '</div>';
    }

    function render() {
      if (!st.loaded) { el.innerHTML = '<div class="hs-empty"><span class="hs-spin"></span>Loading ...</div>'; return; }
      var installed = st.plugins.filter(function (p) { return p.installed; });
      var avail = st.plugins.filter(function (p) { return !p.installed; });
      var html = '';
      if (st.error) html += '<div class="hs-err">' + esc(st.error) + '</div>';
      if (st.notice) html += '<div class="hs-note">' + esc(st.notice) + '</div>';
      var errs = st.catalogErrors ? Object.keys(st.catalogErrors) : [];
      if (errs.length) {
        html += '<div class="hs-note">Skipped in the catalog: ' + esc(errs.map(function (k) { return k + ' (' + st.catalogErrors[k] + ')'; }).join('; ')) + '</div>';
      }
      if (installed.length) {
        html += '<h3>Installed<small>' + installed.length + '</small></h3><div class="hs-grid">' + installed.map(card).join('') + '</div>';
      }
      html += '<h3>Available<small>' + avail.length + '</small></h3>';
      if (avail.length) html += '<div class="hs-grid">' + avail.map(card).join('') + '</div>';
      else html += '<div class="hs-empty">' + (installed.length ? 'Everything available is installed.' : 'No ' + noun + 's found in the catalog.') + '</div>';
      // keep log scroll positions while re-rendering
      var scroll = {};
      Array.prototype.forEach.call(el.querySelectorAll('.hs-log'), function (n) { scroll[n.id] = n.scrollTop; });
      el.innerHTML = html;
      Array.prototype.forEach.call(el.querySelectorAll('.hs-log'), function (n) {
        n.scrollTop = st.job && st.job.state === 'running' ? n.scrollHeight : (scroll[n.id] || 0);
      });
    }

    // ---- events ----------------------------------------------------------------------------
    el.addEventListener('click', function (e) {
      var t = e.target.closest('[data-do]');
      if (!t || t.disabled) return;
      var what = t.getAttribute('data-do'), id = t.getAttribute('data-id');
      if (what === 'install') startJob(id, 'install');
      else if (what === 'update') startJob(id, 'update');
      else if (what === 'repair') startJob(id, 'repair');
      else if (what === 'enable') simple(id, 'enable', undefined, 'enabled');
      else if (what === 'remove') { st.ask = { id: id, kind: 'remove' }; render(); }
      else if (what === 'disable') {
        var p = byId(id);
        var live = (p && p.dependents || []).filter(function (d) { var q = byId(d); return !q || q.running; });
        if (live.length) { st.ask = { id: id, kind: 'disable' }; render(); }
        else simple(id, 'disable', undefined, 'disabled');
      }
      else if (what === 'cancel') { st.ask = null; render(); }
      else if (what === 'dismiss') { st.job = null; render(); }
      else if (what === 'confirm') {
        var act = t.getAttribute('data-act');
        var q = byId(id);
        // "also removes ..." is only sent after the confirm text above has named those plugins
        var cascade = !!(st.ask && st.ask.kind === 'cascade') || !!(q && (q.dependents || []).length);
        st.notice = '';
        if (act === 'uninstall') simple(id, 'uninstall', { cascade: cascade }, 'removed');
        else simple(id, 'disable', undefined, 'disabled');
        if (st.ask && st.ask.kind === 'cascade') st.ask = null;
      }
    });

    render();
    load(true).then(function () {
      // /plugins#twitch -> scroll to that card and flash it
      var m = location.hash && location.hash.slice(1);
      var target = m && document.getElementById('hs-' + m);
      if (target) { target.scrollIntoView({ block: 'center' }); target.classList.add('flash'); setTimeout(function () { target.classList.remove('flash'); }, 2200); }
    });
    var timer = setInterval(function () {
      if (!document.body.contains(el)) { clearInterval(timer); return; }
      if (!(st.job && st.job.state === 'running')) load(false);
    }, 5000);

    return { reload: function () { return load(true); } };
  }

  window.HexStore = { mount: mount };
})();
