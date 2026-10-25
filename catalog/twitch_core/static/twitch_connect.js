/* Twitch connection - the box every Twitch module's tab shows (served by the twitch_core library).
 *
 *   <div id="twitch-conn"></div>
 *   <script src="/plugins/twitch_core/static/twitch_connect.js"></script>
 *   TwitchConnect.mount(document.getElementById('twitch-conn'),
 *                       {back: '/giveaways', label: 'Giveaways', compact: true, needs: ['moderator:read:chatters']});
 *
 * back     the page a sign-in returns to (default: this page)
 * label    the module's name, for "Giveaways needs ..." lines
 * compact  one summary card with a "Set up" button that opens the editor (opened by itself while something is missing)
 * needs    scopes this module cannot work without - a sign-in that lacks one is flagged
 * optional true: the module works without a sign-in (Twitch reads chat anonymously) - not signed in is a note, not a problem
 *
 * The element gets a 'twitchconnection' event (detail: the status) after every refresh, and mount() returns
 * {refresh(), status()}.
 */
(function () {
  'use strict';

  var CSS = ''
    + '.tcx{--tcx-panel:var(--hb-panel,#131319);--tcx-line:var(--hb-line,#232330);--tcx-ink:var(--hb-ink,#e8e8ef);'
    + '--tcx-dim:var(--hb-dim,#8a8a9c);--tcx-accent:var(--hb-accent,#ff3b30);--tcx-good:var(--hb-good,#3ddc84);'
    + '--tcx-warn:var(--hb-warn,#ffb020);--tcx-mono:var(--hb-mono,Consolas,monospace);color:var(--tcx-ink)}'
    + '.tcx [hidden]{display:none!important}'
    + '.tcx-card{background:var(--tcx-panel);border:1px solid var(--tcx-line);border-radius:12px;padding:18px;margin-bottom:16px}'
    + '.tcx-card h2{margin:0 0 4px;font-size:14px;letter-spacing:.14em;text-transform:uppercase}'
    + '.tcx-hint{margin:0 0 14px;color:var(--tcx-dim);font-size:13px}'
    + '.tcx-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap}'
    + '.tcx-head h2{margin:0;flex:0 0 auto}.tcx-head .tcx-sp{flex:1}'
    + '.tcx-pill{font:600 12px/1 var(--tcx-mono);padding:6px 10px;border-radius:99px;border:1px solid var(--tcx-line);color:var(--tcx-dim)}'
    + '.tcx-pill.on{color:var(--tcx-good);border-color:#1d4d33;background:#0e2118}'
    + '.tcx-pill.off{color:var(--tcx-warn);border-color:#4d3a10;background:#211a0b}'
    + '.tcx-lines{margin-top:10px;font:12.5px/1.8 var(--tcx-mono);color:var(--tcx-dim)}'
    + '.tcx-lines .ok{color:var(--tcx-good)}.tcx-lines .bad{color:var(--tcx-warn)}'
    + '.tcx-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}'
    + '.tcx label.tcx-f{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--tcx-dim);letter-spacing:.06em;text-transform:uppercase;font-weight:600}'
    + '.tcx input{background:#08080c;border:1px solid var(--tcx-line);color:var(--tcx-ink);border-radius:8px;padding:9px 10px;font:14px/1.3 inherit;width:100%;box-sizing:border-box}'
    + '.tcx input:focus,.tcx button:focus-visible{outline:2px solid var(--tcx-accent);outline-offset:1px;border-color:var(--tcx-accent)}'
    + '.tcx label.tcx-sw{display:flex;flex-direction:row;align-items:center;gap:7px;font-size:14px;color:var(--tcx-ink)}'
    + '.tcx label.tcx-sw input{width:auto}'
    + '.tcx button.tcx-act{background:var(--tcx-accent);color:#fff;border:0;border-radius:9px;padding:10px 16px;font:700 14px inherit;cursor:pointer}'
    + '.tcx button.tcx-act:hover{filter:brightness(1.12)}'
    + '.tcx button.tcx-sec{background:#1c1c25;color:var(--tcx-ink);border:1px solid var(--tcx-line);border-radius:9px;padding:9px 14px;font:600 13px inherit;cursor:pointer}'
    + '.tcx button.tcx-sec:hover{border-color:var(--tcx-accent)}'
    + '.tcx-row{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin-top:14px}'
    + '.tcx ol{margin:0 0 14px;padding-left:20px;color:var(--tcx-dim);font-size:13.5px}.tcx ol li{margin-bottom:6px}'
    + '.tcx code{color:var(--tcx-good);font:12.5px var(--tcx-mono)}'
    + '.tcx a.tcx-link{color:var(--tcx-ink)}'
    + '.tcx-warnline{border-left:3px solid var(--tcx-warn);padding-left:12px;color:var(--tcx-dim);font-size:13px;margin-top:10px}'
    + '.tcx-ch{display:grid;grid-template-columns:minmax(120px,1.2fr) minmax(100px,1fr) auto auto auto;gap:9px;align-items:center;margin:8px 0 2px}'
    + '.tcx-ch .tcx-chs{grid-column:1/-1;font:12px var(--tcx-mono);color:var(--tcx-dim);margin:-2px 0 8px}'
    + '.tcx-ch .tcx-chs.ok{color:var(--tcx-good)}.tcx-ch .tcx-chs.bad{color:var(--tcx-warn)}'
    + '.tcx-tag{font:700 11px var(--tcx-mono);letter-spacing:.1em;text-transform:uppercase;color:var(--tcx-accent);padding:0 6px}'
    + '.tcx-acct{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:6px 0;font-size:14px}'
    + '.tcx-acct code{font-size:13px}'
    + '.tcx-acct .tcx-miss{flex-basis:100%;font-size:12.5px;color:var(--tcx-warn)}'
    + '@media (max-width:760px){.tcx-ch{grid-template-columns:1fr 1fr}}';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }

  function addStyle() {
    if (document.getElementById('tcx-style')) return;
    var st = document.createElement('style');
    st.id = 'tcx-style';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function api(method, path, body) {
    var o = {method: method, headers: {}};
    if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch(path, o).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  function copy(text, say) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { say('Copied'); }, function () { fallback(); });
    } else { fallback(); }
    function fallback() {
      try {
        var ta = document.createElement('textarea');
        ta.value = text; ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
        document.body.appendChild(ta); ta.focus(); ta.select();
        var ok = document.execCommand('copy');
        document.body.removeChild(ta);
        say(ok ? 'Copied' : 'Copy failed - select it and copy by hand');
      } catch (e) { say('Copy failed - select it and copy by hand'); }
    }
  }

  // One line about a channel: connected how, as whom.
  function chLine(c) {
    if (!c) return {t: 'not saved yet', cls: ''};
    if (!c.enabled) return {t: 'off', cls: ''};
    if (!c.connected) return {t: 'not connected' + (c.last_error ? ' - ' + c.last_error : ''), cls: 'bad'};
    var live = c.live === true ? ' - LIVE' : '';
    if (c.source === 'eventsub') {
      return {t: 'connected' + live + ' - ' + (c.own_account ? 'full access, as its own account (' + c.account + ')'
                                                  : 'chat as ' + c.account + ' - sign in as ' + c.login + ' for its alerts'), cls: 'ok'};
    }
    return {t: 'connected - chat only (anonymous)', cls: 'ok'};
  }

  function mount(el, opts) {
    opts = opts || {};
    addStyle();
    var say = opts.toast || window.toast || function () {};
    var back = opts.back || location.pathname;
    var label = opts.label || 'This module';
    var needs = opts.needs || [];
    var last = null, rows = [], dirty = false, opened = !opts.compact, timer = null;

    el.classList.add('tcx');
    el.innerHTML = ''
      + '<div class="tcx-card">'
      +   '<div class="tcx-head"><h2>Twitch connection</h2><span class="tcx-pill" data-x="pill">checking</span>'
      +     '<span class="tcx-sp"></span><button class="tcx-sec" data-x="toggle"' + (opts.compact ? '' : ' hidden') + '>Set up</button></div>'
      +   '<div class="tcx-lines" data-x="lines"></div>'
      + '</div>'
      + '<div data-x="full"' + (opened ? '' : ' hidden') + '>'
      +   '<div class="tcx-card"><h2>Twitch app</h2>'
      +     '<p class="tcx-hint">Twitch needs its own app registration before it lets HexCast read your events or post in chat. One-time setup, about two minutes - every Twitch module shares it.</p>'
      +     '<ol><li>Go to <a class="tcx-link" href="https://dev.twitch.tv/console/apps/create" target="_blank" rel="noopener">dev.twitch.tv/console/apps/create</a></li>'
      +     '<li>Name it anything. Category: <code>Broadcaster Suite</code>. Client Type: <code>Confidential</code>.</li>'
      +     '<li>Add this exact OAuth Redirect URL: <code data-x="redir"></code></li>'
      +     '<li>Create it, then copy the Client ID and generate a Client Secret.</li></ol>'
      +     '<div class="tcx-grid">'
      +       '<label class="tcx-f">Client ID<input data-x="cid" placeholder="paste from Twitch" autocomplete="off"></label>'
      +       '<label class="tcx-f">Client secret<input data-x="csec" type="password" placeholder="paste from Twitch" autocomplete="off"></label>'
      +     '</div>'
      +     '<div class="tcx-row"><button class="tcx-act" data-x="save-creds">Save credentials</button>'
      +       '<button class="tcx-sec" data-x="copy-redir">Copy redirect URL</button></div>'
      +     '<p class="tcx-warnline">Stored in <code>config/twitch_secrets.json</code>. HexCast has no login, so keep the port on your LAN only. Twitch takes plain <code>http</code> only for <code>localhost</code>: open this page as <code>http://localhost:...</code> on the Hexcast PC to sign in.</p>'
      +   '</div>'
      +   '<div class="tcx-card"><h2>Channels</h2>'
      +     '<p class="tcx-hint">The channels HexCast watches (up to 5). The first is the <b>primary</b> - what every module uses unless told otherwise. A channel uses the account with its own name if you signed in as it (all its alerts, chat as the broadcaster); otherwise your default account (its chat, and what that user may do there as a moderator), or anonymous chat when nobody is signed in.</p>'
      +     '<div data-x="rows"></div>'
      +     '<div class="tcx-row"><button class="tcx-sec" data-x="add">Add channel</button>'
      +       '<button class="tcx-act" data-x="save-ch">Save channels</button></div>'
      +   '</div>'
      +   '<div class="tcx-card"><h2>Sign in</h2>'
      +     '<p class="tcx-hint">Signing in unlocks follows, subs, bits, raids and redeems, posting in chat, and seeing who is in chat - for the channels that account owns or moderates. Without it you still get chat, read anonymously. To add another account, sign in to Twitch as that user in this browser first (a private window works), then connect.</p>'
      +     '<div data-x="accts"></div>'
      +     '<div class="tcx-row"><button class="tcx-act" data-x="login">Connect a Twitch account</button>'
      +       '<button class="tcx-sec" data-x="logout-all">Sign out of all</button>'
      +       '<button class="tcx-sec" data-x="reconnect">Reconnect</button></div>'
      +   '</div>'
      + '</div>';

    function $(name) { return el.querySelector('[data-x="' + name + '"]'); }

    function readRows() {
      rows = [].map.call(el.querySelectorAll('.tcx-ch'), function (row) {
        return {login: row.querySelector('.tcx-login').value.trim().replace(/^#/, '').toLowerCase(),
                label: row.querySelector('.tcx-label').value.trim(),
                enabled: row.querySelector('.tcx-on').checked,
                saved: row.getAttribute('data-saved') || ''};
      });
    }

    function renderRows() {
      $('rows').innerHTML = rows.map(function (r, i) {
        return '<div class="tcx-ch" data-saved="' + esc(r.saved || '') + '">'
          + '<input class="tcx-login" value="' + esc(r.login) + '" placeholder="channel name" maxlength="25">'
          + '<input class="tcx-label" value="' + esc(r.label || '') + '" placeholder="label (optional)" maxlength="24">'
          + '<label class="tcx-sw"><input type="checkbox" class="tcx-on"' + (r.enabled !== false ? ' checked' : '') + '> on</label>'
          + (i ? '<button class="tcx-sec" data-up="' + i + '" title="Make this the primary channel">Make primary</button>'
               : '<span class="tcx-tag">primary</span>')
          + '<button class="tcx-sec" data-del="' + i + '" title="Remove this channel">&times;</button>'
          + '<div class="tcx-chs" data-login="' + esc(r.saved || '') + '"></div></div>';
      }).join('') || '<p class="tcx-hint">No channel yet - add one here, or sign in and your own channel is used.</p>';
      paintRows();
    }

    function paintRows() {
      var by = {};
      ((last && last.channels) || []).forEach(function (c) { by[c.login] = c; });
      el.querySelectorAll('.tcx-chs').forEach(function (s) {
        var login = s.getAttribute('data-login');
        var l = login ? chLine(by[login]) : {t: 'not saved yet', cls: ''};
        s.textContent = l.t;
        s.className = 'tcx-chs' + (l.cls ? ' ' + l.cls : '');
      });
    }

    function paintAccounts(s) {
      var a = s.accounts || [];
      $('accts').innerHTML = a.length ? a.map(function (x) {
        var miss = (x.missing || []);
        return '<div class="tcx-acct"><span>signed in as <code>' + esc(x.login) + '</code>'
             + (x.login === s.default_account && a.length > 1 ? ' <span class="tcx-tag">default</span>' : '') + '</span>'
             + '<button class="tcx-sec" data-logout="' + esc(x.login) + '">Sign out</button>'
             + (miss.length ? '<span class="tcx-miss">signed in before Hexcast asked for ' + esc(miss.join(', '))
                              + ' - connect this account again to allow it</span>' : '')
             + '</div>';
      }).join('') : '<p class="tcx-hint">Not signed in - chat is read anonymously.</p>';
    }

    function paintSummary(s) {
      var chans = s.channels || [], on = chans.filter(function (c) { return c.enabled; });
      var up = on.filter(function (c) { return c.connected; }).length;
      var accts = s.accounts || [];
      var problems = [];
      if (!s.has_credentials) problems.push('Add your Twitch app\'s client ID and secret.');
      var notes = [];
      if (!accts.length) (opts.optional ? notes : problems).push(opts.optional
        ? 'Not signed in - chat is read anonymously; sign in for alerts, shoutouts and posting in chat.'
        : 'Not signed in - ' + label + ' needs a signed-in Twitch account.');
      if (!chans.length) problems.push('No channel set.');
      var dflt = accts.filter(function (a) { return a.login === s.default_account; })[0];
      var lacking = dflt ? needs.filter(function (n) { return (dflt.missing || []).indexOf(n) >= 0; }) : [];
      if (lacking.length) problems.push(label + ' needs ' + lacking.join(', ') + ': connect ' + dflt.login + ' again to allow it.');

      var pill = $('pill');
      var good = !problems.length && on.length > 0 && up === on.length;
      pill.textContent = !s.has_credentials ? 'not set up' : !accts.length && !opts.optional ? 'not signed in'
                       : on.length ? up + ' of ' + on.length + ' connected' : 'no channel';
      pill.className = 'tcx-pill ' + (good ? 'on' : 'off');

      var html = chans.map(function (c) {
        var l = chLine(c);
        return '<div class="' + l.cls + '">#' + esc(c.login) + (c.label ? ' (' + esc(c.label) + ')' : '')
             + (c.primary && chans.length > 1 ? ' - primary' : '') + ' - ' + esc(l.t) + '</div>';
      }).join('');
      html += notes.map(function (p) { return '<div>' + esc(p) + '</div>'; }).join('');
      html += problems.map(function (p) { return '<div class="bad">' + esc(p) + '</div>'; }).join('');
      $('lines').innerHTML = html;
      if (opts.compact && problems.length && !opened && !el._tcxTouched) setOpen(true);
    }

    function setOpen(on) {
      opened = on;
      $('full').hidden = !on;
      $('toggle').textContent = on ? 'Hide' : 'Set up';
    }

    function paint(s) {
      last = s;
      $('redir').textContent = s.redirect_uri || '';
      if ($('cid') !== document.activeElement && !$('cid').value) $('cid').value = s.client_id || '';
      var sig = JSON.stringify((s.channels || []).map(function (c) { return [c.login, c.label, c.enabled]; }));
      if (!dirty && sig !== el._tcxSig && !$('rows').contains(document.activeElement)) {
        // (a Hexcast with no channel set watches the signed-in user's own channel: it shows as the first row)
        el._tcxSig = sig;
        rows = (s.channels || []).map(function (c) {
          return {login: c.login, label: c.label, enabled: c.enabled, saved: c.login};
        });
        renderRows();
      } else {
        paintRows();
      }
      paintAccounts(s);
      paintSummary(s);
      try { el.dispatchEvent(new CustomEvent('twitchconnection', {detail: s})); } catch (e) {}
    }

    function refresh() {
      return api('GET', '/twitch/api/connection').then(paint).catch(function (e) {
        $('pill').textContent = 'not responding';
        $('pill').className = 'tcx-pill off';
      });
    }

    el.addEventListener('input', function (e) { if (e.target.closest('.tcx-ch')) dirty = true; });
    el.addEventListener('change', function (e) { if (e.target.closest('.tcx-ch')) dirty = true; });
    el.addEventListener('click', function (e) {
      var t = e.target.closest('button');
      if (!t || !el.contains(t)) return;
      var x = t.getAttribute('data-x');
      if (t.hasAttribute('data-del')) { readRows(); rows.splice(+t.getAttribute('data-del'), 1); dirty = true; renderRows(); return; }
      if (t.hasAttribute('data-up')) { readRows(); var i = +t.getAttribute('data-up'); rows.unshift(rows.splice(i, 1)[0]); dirty = true; renderRows(); return; }
      if (t.hasAttribute('data-logout')) {
        api('POST', '/twitch/auth/logout', {login: t.getAttribute('data-logout')}).then(function () { say('Signed out'); refresh(); });
        return;
      }
      if (x === 'toggle') { el._tcxTouched = true; setOpen(!opened); }
      else if (x === 'copy-redir') copy($('redir').textContent, say);
      else if (x === 'save-creds') {
        api('POST', '/twitch/api/credentials', {client_id: $('cid').value, client_secret: $('csec').value})
          .then(function () { $('csec').value = ''; say('Credentials saved'); refresh(); })
          .catch(function (err) { say(err.message); });
      }
      else if (x === 'add') {
        readRows();
        if (rows.length >= ((last && last.max_channels) || 5)) { say('At most ' + ((last && last.max_channels) || 5) + ' channels'); return; }
        rows.push({login: '', label: '', enabled: true, saved: ''});
        dirty = true; renderRows();
        var inputs = el.querySelectorAll('.tcx-login'); if (inputs.length) inputs[inputs.length - 1].focus();
      }
      else if (x === 'save-ch') {
        readRows();
        var out = rows.filter(function (r) { return r.login; }).map(function (r) { return {login: r.login, label: r.label, enabled: r.enabled}; });
        api('POST', '/twitch/api/connection', {channels: out}).then(function (s) {
          dirty = false; el._tcxSig = null; paint(s); say('Channels saved');
        }).catch(function (err) { say(err.message); });
      }
      else if (x === 'login') {
        if (last && !last.has_credentials) { say('Save your client ID and secret first'); return; }
        location.href = '/twitch/auth/login?next=' + encodeURIComponent(back);
      }
      else if (x === 'logout-all') api('POST', '/twitch/auth/logout').then(function () { say('Signed out of all accounts'); refresh(); });
      else if (x === 'reconnect') api('POST', '/twitch/api/reconnect').then(function () { say('Reconnecting'); setTimeout(refresh, 800); });
    });

    refresh();
    timer = setInterval(refresh, opts.every || 5000);
    return {refresh: refresh, status: function () { return last; }, stop: function () { clearInterval(timer); }};
  }

  window.TwitchConnect = {mount: mount};
})();
