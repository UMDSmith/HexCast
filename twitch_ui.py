"""HTML for the HexCast Twitch panel and the two OBS browser sources."""

# ==========================================================================
# shared overlay helpers (injected into both overlays)
# ==========================================================================

_OVERLAY_BOOT = r"""
function loadFont(name){
  if(!name) return;
  var id = 'font-' + name.replace(/[^a-z0-9]/gi,'');
  if(document.getElementById(id)) return;
  var l = document.createElement('link');
  l.id = id; l.rel = 'stylesheet';
  l.href = 'https://fonts.googleapis.com/css2?family=' +
           encodeURIComponent(name).replace(/%20/g,'+') +
           ':wght@400;500;600;700;800;900&display=swap';
  document.head.appendChild(l);
}
function connect(path, onMsg){
  var proto = location.protocol === 'https:' ? 'wss' : 'ws';
  var ws = new WebSocket(proto + '://' + location.host + path);
  ws.onmessage = function(e){ try { onMsg(JSON.parse(e.data)); } catch(err){} };
  ws.onclose = function(){ setTimeout(function(){ connect(path, onMsg); }, 1500); };
  ws.onerror = function(){ try { ws.close(); } catch(err){} };
  return ws;
}
function esc(s){
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function hexToRgba(hex, alpha){
  var h = String(hex || '#000000').replace('#','');
  if(h.length === 3) h = h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
  var n = parseInt(h, 16);
  if(isNaN(n)) return 'rgba(0,0,0,' + alpha + ')';
  return 'rgba(' + ((n>>16)&255) + ',' + ((n>>8)&255) + ',' + (n&255) + ',' + alpha + ')';
}
"""


# ==========================================================================
# chat overlay
# ==========================================================================

CHAT_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>HexCast chat</title>
<style>
  html,body{margin:0;padding:0;background:transparent;overflow:hidden;height:100%;}
  #wrap{
    position:absolute; inset:0;
    display:flex; flex-direction:column;
    padding:8px; box-sizing:border-box;
  }
  #wrap.bottom{ justify-content:flex-end; }
  #wrap.top{ justify-content:flex-start; }
  #list{ display:flex; flex-direction:column; gap:var(--gap,8px); width:var(--width,100%); }
  #wrap.left  #list{ margin-right:auto; align-items:flex-start; text-align:left; }
  #wrap.center #list{ margin:0 auto;     align-items:center;    text-align:center; }
  #wrap.right #list{ margin-left:auto;  align-items:flex-end;  text-align:right; }

  .msg{
    font-family:var(--font), 'Segoe UI', system-ui, sans-serif;
    font-size:var(--size,28px);
    font-weight:var(--weight,600);
    line-height:var(--lh,1.35);
    color:var(--fg,#f2f2f7);
    background:var(--bubble, transparent);
    border-radius:var(--radius,14px);
    padding:var(--pad,12px);
    max-width:100%;
    word-wrap:break-word;
    overflow-wrap:anywhere;
    -webkit-text-stroke:var(--stroke,0px) var(--stroke-color,#000);
    paint-order:stroke fill;
    text-shadow:var(--shadow,none);
    transform-origin:left center;
  }
  .msg.first{ box-shadow: inset 4px 0 0 0 var(--highlight,#ff3b30); }
  .msg.stacked .name{ display:block; margin-bottom:2px; }
  .name{ font-weight:var(--nameweight,800); }
  .colon{ opacity:.55; margin-right:.28em; }
  .badge{ height:1em; vertical-align:-0.14em; margin-right:.22em; }
  .emote{ height:var(--emote,34px); vertical-align:middle; margin:-0.25em 2px; }
  .mention{ color:var(--highlight,#ff3b30); font-weight:800; }
  .cheer{ color:#c99cff; font-weight:800; }
  .time{ opacity:.45; font-size:.7em; margin-right:.4em; font-variant-numeric:tabular-nums; }
  .reply{ display:block; font-size:.62em; opacity:.55; margin-bottom:2px; }

  .msg.leaving{ opacity:0; transition:opacity var(--fadedur,.5s) ease; }
  @keyframes slideIn{ from{ opacity:0; transform:translateX(-14px);} to{ opacity:1; transform:none;} }
  @keyframes fadeIn { from{ opacity:0; } to{ opacity:1; } }
  @keyframes popIn  { 0%{ opacity:0; transform:scale(.86);} 60%{ transform:scale(1.03);} 100%{ opacity:1; transform:scale(1);} }
  .anim-slide{ animation:slideIn .28s cubic-bezier(.2,.8,.3,1) both; }
  .anim-fade { animation:fadeIn .3s ease both; }
  .anim-pop  { animation:popIn .32s cubic-bezier(.2,.9,.3,1.2) both; }
  @media (prefers-reduced-motion: reduce){ .msg{ animation:none !important; } }
</style></head>
<body>
<div id="wrap"><div id="list"></div></div>
<script>
__BOOT__

var CFG = null;
var wrap = document.getElementById('wrap');
var list = document.getElementById('list');

function applyConfig(cfg){
  CFG = cfg;
  var c = cfg.chat, r = document.documentElement.style;
  loadFont(c.font_family);
  r.setProperty('--font', '"' + c.font_family + '"');
  r.setProperty('--size', c.font_size + 'px');
  r.setProperty('--weight', c.font_weight);
  r.setProperty('--nameweight', c.name_weight);
  r.setProperty('--lh', c.line_height);
  r.setProperty('--fg', c.text_color);
  r.setProperty('--gap', c.gap + 'px');
  r.setProperty('--pad', c.padding + 'px');
  r.setProperty('--radius', c.bubble_radius + 'px');
  r.setProperty('--emote', c.emote_size + 'px');
  r.setProperty('--highlight', c.highlight_color);
  r.setProperty('--width', c.width_percent + '%');
  r.setProperty('--fadedur', c.fade_duration + 's');
  r.setProperty('--bubble', c.bubble ? hexToRgba(c.bubble_color, c.bubble_opacity) : 'transparent');
  r.setProperty('--stroke', c.outline ? c.outline_width + 'px' : '0px');
  r.setProperty('--stroke-color', c.outline_color);
  r.setProperty('--shadow', c.shadow ? '0 2px 6px rgba(0,0,0,.85)' : 'none');
  wrap.className = (c.direction === 'top' ? 'top' : 'bottom') + ' ' + c.align;
  trim();
}

function renderFragments(frags){
  return (frags || []).map(function(f){
    if(f.t === 'emote') return '<img class="emote" src="' + esc(f.url) + '" alt="' + esc(f.name) + '">';
    if(f.t === 'mention') return '<span class="mention">' + esc(f.v) + '</span>';
    if(f.t === 'cheer') return '<span class="cheer">' + esc(f.v) + '</span>';
    return esc(f.v);
  }).join('');
}

function nameColor(msg){
  var c = CFG.chat;
  if(c.name_color_mode === 'custom') return c.name_color;
  return msg.user.color || c.name_color;
}

function addMessage(msg){
  if(!CFG) return;
  var c = CFG.chat;
  var el = document.createElement('div');
  el.className = 'msg anim-' + c.animation + (c.layout === 'stacked' ? ' stacked' : '');
  if(c.highlight_first && msg.flags && msg.flags.first) el.className += ' first';
  el.dataset.id = msg.id;

  var html = '';
  if(msg.reply) html += '<span class="reply">replying to ' + esc(msg.reply.user) + '</span>';
  if(c.show_timestamps){
    var d = new Date(msg.ts * 1000);
    html += '<span class="time">' + String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0') + '</span>';
  }
  if(c.show_badges && msg.user.badges){
    msg.user.badges.forEach(function(b){
      html += '<img class="badge" src="' + esc(b.url) + '" title="' + esc(b.title) + '">';
    });
  }
  html += '<span class="name" style="color:' + esc(nameColor(msg)) + '">' + esc(msg.user.name) + '</span>';
  html += '<span class="colon">' + (c.layout === 'stacked' ? '' : ':') + '</span>';
  html += renderFragments(msg.fragments);
  el.innerHTML = html;

  list.appendChild(el);
  trim();

  if(c.fade_after > 0){
    setTimeout(function(){
      el.classList.add('leaving');
      setTimeout(function(){ el.remove(); }, c.fade_duration * 1000 + 60);
    }, c.fade_after * 1000);
  }
}

function trim(){
  if(!CFG) return;
  var max = CFG.chat.max_messages || 25;
  while(list.children.length > max) list.removeChild(list.firstChild);
}

connect('/twitch/ws/chat', function(m){
  if(m.type === 'config') applyConfig(m.config);
  else if(m.type === 'chat') addMessage(m);
  else if(m.type === 'clear') list.innerHTML = '';
  else if(m.type === 'delete'){
    var n = list.querySelector('[data-id="' + m.id + '"]');
    if(n) n.remove();
  }
});
</script></body></html>
""".replace("__BOOT__", _OVERLAY_BOOT)


# ==========================================================================
# events overlay
# ==========================================================================

EVENTS_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>HexCast alerts</title>
<style>
  html,body{margin:0;padding:0;background:transparent;overflow:hidden;height:100%;}
  #stage{
    position:absolute; inset:0; display:flex; padding:24px; box-sizing:border-box;
  }
  #stage.left{ justify-content:flex-start; } #stage.center{ justify-content:center; } #stage.right{ justify-content:flex-end; }
  #stage.top{ align-items:flex-start; } #stage.middle{ align-items:center; } #stage.bottom{ align-items:flex-end; }

  .alert{
    font-family:var(--font), 'Segoe UI', system-ui, sans-serif;
    color:var(--fg,#fff);
    background:var(--bubble, transparent);
    border-radius:var(--radius,18px);
    padding:22px 34px;
    text-align:center;
    max-width:90vw;
    -webkit-text-stroke:var(--stroke,0) var(--stroke-color,#000);
    paint-order:stroke fill;
    text-shadow:0 3px 10px rgba(0,0,0,.6);
    border-top:3px solid var(--accent,#ff3b30);
  }
  .title{
    font-size:var(--sub,26px); font-weight:800; letter-spacing:.14em;
    text-transform:uppercase; color:var(--accent,#ff3b30); margin-bottom:6px;
  }
  .body{ font-size:var(--size,40px); font-weight:800; line-height:1.15; }
  .note{ font-size:var(--sub,26px); font-weight:500; opacity:.82; margin-top:10px; font-style:italic; }

  @keyframes popIn{ 0%{opacity:0; transform:scale(.8) translateY(14px);} 60%{transform:scale(1.04);} 100%{opacity:1; transform:none;} }
  @keyframes slideIn{ from{opacity:0; transform:translateY(-30px);} to{opacity:1; transform:none;} }
  @keyframes fadeIn{ from{opacity:0;} to{opacity:1;} }
  .anim-pop{ animation:popIn .42s cubic-bezier(.2,.9,.3,1.25) both; }
  .anim-slide{ animation:slideIn .38s cubic-bezier(.2,.8,.3,1) both; }
  .anim-fade{ animation:fadeIn .4s ease both; }
  .out{ opacity:0; transform:scale(.96); transition:opacity .35s ease, transform .35s ease; }
  @media (prefers-reduced-motion: reduce){ .alert{ animation:none !important; } }
</style></head>
<body>
<div id="stage"></div>
<script>
__BOOT__

var CFG = null, queue = [], busy = false;
var stage = document.getElementById('stage');

function applyConfig(cfg){
  CFG = cfg;
  var e = cfg.events, r = document.documentElement.style;
  loadFont(e.font_family);
  r.setProperty('--font', '"' + e.font_family + '"');
  r.setProperty('--size', e.font_size + 'px');
  r.setProperty('--sub', e.sub_font_size + 'px');
  r.setProperty('--fg', e.text_color);
  r.setProperty('--accent', e.accent_color);
  r.setProperty('--radius', e.bubble_radius + 'px');
  r.setProperty('--bubble', hexToRgba(e.bubble_color, e.bubble_opacity));
  r.setProperty('--stroke', e.outline ? '2px' : '0');
  r.setProperty('--stroke-color', e.outline_color);
  stage.className = e.align + ' ' + e.valign;
}

function show(ev){
  var e = CFG.events;
  var el = document.createElement('div');
  el.className = 'alert anim-' + e.animation;
  var html = '<div class="title">' + esc(ev.title) + '</div>';
  html += '<div class="body">' + esc(ev.body) + '</div>';
  if(ev.message) html += '<div class="note">' + esc(ev.message) + '</div>';
  el.innerHTML = html;
  stage.appendChild(el);

  setTimeout(function(){
    el.classList.add('out');
    setTimeout(function(){
      el.remove();
      setTimeout(next, (CFG.events.gap_between || 0) * 1000);
    }, 380);
  }, (ev.duration || e.default_duration) * 1000);
}

function next(){
  if(!queue.length){ busy = false; return; }
  busy = true;
  show(queue.shift());
}

connect('/twitch/ws/events', function(m){
  if(m.type === 'config'){ applyConfig(m.config); return; }
  if(m.type !== 'event' || !CFG) return;
  queue.push(m);
  if(!busy) next();
});
</script></body></html>
""".replace("__BOOT__", _OVERLAY_BOOT)


# ==========================================================================
# control panel
# ==========================================================================

PANEL_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HexCast - Twitch</title>
<style>
  :root{
    --bg:#0a0a0e; --panel:#131319; --line:#232330; --ink:#e8e8ef;
    --dim:#8a8a9c; --accent:#ff3b30; --good:#3ddc84; --warn:#ffb020;
    --mono:'SFMono-Regular',Consolas,'Liberation Mono',monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.5 'Segoe UI',system-ui,-apple-system,sans-serif;}
  header{display:flex;align-items:center;gap:14px;padding:16px 22px;border-bottom:1px solid var(--line);
         position:sticky;top:0;background:var(--bg);z-index:5}
  header h1{margin:0;font-size:17px;letter-spacing:.2em;text-transform:uppercase;font-weight:800}
  header h1 b{color:var(--accent)}
  .pill{font:600 12px/1 var(--mono);padding:6px 10px;border-radius:99px;border:1px solid var(--line);color:var(--dim)}
  .pill.on{color:var(--good);border-color:#1d4d33;background:#0e2118}
  .pill.off{color:var(--warn);border-color:#4d3a10;background:#211a0b}
  .spacer{flex:1}
  a.ghost{color:var(--dim);text-decoration:none;font-size:13px;border:1px solid var(--line);
          padding:6px 11px;border-radius:8px}
  a.ghost:hover{color:var(--ink);border-color:var(--accent)}

  nav{display:flex;gap:4px;padding:14px 22px 0;flex-wrap:wrap}
  nav button{background:none;border:0;border-bottom:2px solid transparent;color:var(--dim);
             font:600 13px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;
             padding:10px 14px;cursor:pointer}
  nav button.sel{color:var(--ink);border-bottom-color:var(--accent)}

  main{padding:18px 22px 60px;max-width:1080px}
  section{display:none} section.sel{display:block}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px}
  .card h2{margin:0 0 4px;font-size:14px;letter-spacing:.14em;text-transform:uppercase}
  .card p.hint{margin:0 0 14px;color:var(--dim);font-size:13px}

  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
  label.f{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--dim);
          letter-spacing:.06em;text-transform:uppercase;font-weight:600}
  input,select,textarea{background:#08080c;border:1px solid var(--line);color:var(--ink);
        border-radius:8px;padding:9px 10px;font:14px/1.3 inherit;width:100%}
  input:focus,select:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
  input[type=color]{padding:3px;height:38px;cursor:pointer}
  label.sw{flex-direction:row;align-items:center;gap:9px;text-transform:none;font-size:14px;color:var(--ink)}
  label.sw input{width:auto}

  button.act{background:var(--accent);color:#fff;border:0;border-radius:9px;padding:10px 16px;
             font:700 14px inherit;cursor:pointer}
  button.act:hover{filter:brightness(1.12)}
  button.sec{background:#1c1c25;color:var(--ink);border:1px solid var(--line);border-radius:9px;
             padding:9px 14px;font:600 13px inherit;cursor:pointer}
  button.sec:hover{border-color:var(--accent)}
  .row{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin-top:14px}

  .url{display:flex;gap:8px;align-items:center;margin:8px 0}
  .url code{flex:1;background:#08080c;border:1px solid var(--line);border-radius:8px;
            padding:9px 11px;font:13px var(--mono);color:var(--good);overflow:auto;white-space:nowrap}

  table.al{width:100%;border-collapse:collapse;font-size:13px}
  table.al th{text-align:left;font:600 11px var(--mono);letter-spacing:.1em;text-transform:uppercase;
              color:var(--dim);padding:8px 6px;border-bottom:1px solid var(--line)}
  table.al td{padding:6px;border-bottom:1px solid #1a1a22;vertical-align:middle}
  table.al input{padding:7px 8px;font-size:13px}
  table.al td.k{font:600 13px var(--mono);color:var(--ink);white-space:nowrap}

  #log{font:12px/1.7 var(--mono);color:var(--dim);background:#08080c;border:1px solid var(--line);
       border-radius:8px;padding:12px;max-height:230px;overflow:auto;white-space:pre-wrap}
  #preview{background:#08080c;border:1px solid var(--line);border-radius:8px;padding:12px;
           max-height:200px;overflow:auto;font-size:13px}
  #preview .m{padding:2px 0;border-bottom:1px solid #15151d}
  #preview b{color:var(--accent)}

  #toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,80px);background:var(--accent);
         color:#fff;padding:11px 20px;border-radius:99px;font-weight:700;font-size:14px;
         transition:transform .25s ease;z-index:20}
  #toast.up{transform:translate(-50%,0)}
  .warn{border-left:3px solid var(--warn);padding-left:12px;color:var(--dim);font-size:13px;margin-top:10px}
  ol.steps{margin:0 0 14px;padding-left:20px;color:var(--dim);font-size:13.5px}
  ol.steps li{margin-bottom:6px}
  ol.steps code{color:var(--good);font:12.5px var(--mono)}
</style></head>
<body>

<header>
  <h1>Hex<b>Cast</b> &nbsp;/&nbsp; Twitch</h1>
  <span class="pill" id="st-conn">connecting</span>
  <span class="pill" id="st-src">-</span>
  <div class="spacer"></div>
  <a class="ghost" href="/">Soundboard</a>
</header>

<nav>
  <button data-tab="conn" class="sel">Connection</button>
  <button data-tab="chat">Chat overlay</button>
  <button data-tab="events">Alert overlay</button>
  <button data-tab="alerts">Alerts</button>
  <button data-tab="status">Status</button>
</nav>

<main>

<!-- ============ CONNECTION ============ -->
<section id="tab-conn" class="sel">
  <div class="card">
    <h2>Twitch app</h2>
    <p class="hint">Twitch needs its own app registration before it will let HexCast read your events. One-time setup, takes about two minutes.</p>
    <ol class="steps">
      <li>Go to <a class="ghost" style="padding:2px 6px" href="https://dev.twitch.tv/console/apps/create" target="_blank">dev.twitch.tv/console/apps/create</a></li>
      <li>Name it anything. Category: <code>Broadcaster Suite</code>. Client Type: <code>Confidential</code>.</li>
      <li>Add this exact OAuth Redirect URL: <code id="redir">__REDIRECT_URI__</code></li>
      <li>Create it, then copy the Client ID and generate a Client Secret.</li>
    </ol>
    <div class="grid">
      <label class="f">Client ID<input id="client_id" placeholder="paste from Twitch"></label>
      <label class="f">Client secret<input id="client_secret" type="password" placeholder="paste from Twitch"></label>
    </div>
    <div class="row">
      <button class="act" id="save-creds">Save credentials</button>
      <button class="sec" id="copy-redir">Copy redirect URL</button>
    </div>
    <p class="warn">These are stored in <code>config/twitch_secrets.json</code>. HexCast has no login, so keep the port on your LAN only.</p>
  </div>

  <div class="card">
    <h2>Sign in</h2>
    <p class="hint">Signing in unlocks follows, subs, gifts, bits, raids and channel point redeems. Without it you still get chat, read anonymously.</p>
    <div class="grid">
      <label class="f">Channel to watch<input id="channel" placeholder="your twitch username"></label>
    </div>
    <div class="row">
      <button class="act" id="login">Connect Twitch account</button>
      <button class="sec" id="logout">Sign out</button>
      <button class="sec" id="reconnect">Reconnect</button>
    </div>
  </div>

  <div class="card">
    <h2>Browser sources</h2>
    <p class="hint">Add each as a Browser source in OBS. Size the source however you want the box to be, then drag it into place. Turn off "Shutdown source when not visible".</p>
    <div class="url"><code id="u-chat"></code><button class="sec" data-copy="u-chat">Copy</button></div>
    <div class="url"><code id="u-events"></code><button class="sec" data-copy="u-events">Copy</button></div>
  </div>

  <div class="card">
    <h2>Send events elsewhere</h2>
    <p class="hint">Every chat message and alert is POSTed as JSON to this URL. Leave blank to skip. Useful for feeding a bot or a local model.</p>
    <div class="grid">
      <label class="f">Forward URL<input id="forward_url" placeholder="http://127.0.0.1:8200/hex/twitch"></label>
      <label class="f">HexCast base URL<input id="hexcast_url"></label>
    </div>
    <div class="row"><button class="act" id="save-conn">Save</button></div>
  </div>
</section>

<!-- ============ CHAT ============ -->
<section id="tab-chat">
  <div class="card">
    <h2>Type</h2>
    <div class="grid" id="g-chat-type"></div>
  </div>
  <div class="card">
    <h2>Colours and box</h2>
    <div class="grid" id="g-chat-look"></div>
  </div>
  <div class="card">
    <h2>Behaviour</h2>
    <div class="grid" id="g-chat-behave"></div>
  </div>
  <div class="card">
    <h2>Filters</h2>
    <div class="grid" id="g-chat-filter"></div>
    <div class="row">
      <button class="act" id="save-chat">Save chat settings</button>
      <button class="sec" id="test-chat">Send a test message</button>
    </div>
  </div>
</section>

<!-- ============ EVENTS LOOK ============ -->
<section id="tab-events">
  <div class="card">
    <h2>Alert appearance</h2>
    <div class="grid" id="g-events"></div>
    <div class="row">
      <button class="act" id="save-events">Save alert settings</button>
      <button class="sec" data-test="follow">Test follow</button>
      <button class="sec" data-test="subscribe">Test sub</button>
      <button class="sec" data-test="cheer">Test bits</button>
      <button class="sec" data-test="raid">Test raid</button>
      <button class="sec" data-test="redeem">Test redeem</button>
    </div>
  </div>
</section>

<!-- ============ ALERTS ============ -->
<section id="tab-alerts">
  <div class="card">
    <h2>What fires, and what it says</h2>
    <p class="hint">Placeholders: <code style="color:var(--good);font-family:var(--mono)">{user} {amount} {tier} {months} {reward}</code>. Put a HexCast clip name in the last column to fire a sound or gif with the alert.</p>
    <table class="al"><thead><tr>
      <th>Event</th><th style="width:52px">On</th><th style="width:74px">Secs</th>
      <th>Title</th><th>Body</th><th>Clip</th><th style="width:60px"></th>
    </tr></thead><tbody id="alerts-body"></tbody></table>
    <div class="row"><button class="act" id="save-alerts">Save alerts</button></div>
  </div>
</section>

<!-- ============ STATUS ============ -->
<section id="tab-status">
  <div class="card">
    <h2>Live chat</h2>
    <div id="preview"></div>
  </div>
  <div class="card">
    <h2>Subscriptions</h2>
    <div id="subs" style="font:12.5px/1.8 var(--mono);color:var(--dim)"></div>
  </div>
  <div class="card">
    <h2>Log</h2>
    <div id="log"></div>
  </div>
</section>

</main>
<div id="toast"></div>

<script>
var CFG = null;
var $ = function(id){ return document.getElementById(id); };

function toast(msg){
  var t = $('toast'); t.textContent = msg; t.classList.add('up');
  clearTimeout(t._t); t._t = setTimeout(function(){ t.classList.remove('up'); }, 1800);
}
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ---------- field definitions ---------- */
var FONTS = ['Inter','Roboto','Montserrat','Oswald','Rubik','Nunito','Poppins','Bebas Neue',
             'Fredoka','Baloo 2','Luckiest Guy','Titan One','Press Start 2P','Bangers',
             'Archivo Black','Space Grotesk','JetBrains Mono','Comic Neue'];

var CHAT_TYPE = [
  ['font_family','Font',{t:'font'}],
  ['font_size','Message size',{t:'num',min:10,max:120}],
  ['font_weight','Message weight',{t:'sel',opts:[[400,'Regular'],[500,'Medium'],[600,'Semibold'],[700,'Bold'],[800,'Extra bold'],[900,'Black']]}],
  ['name_weight','Name weight',{t:'sel',opts:[[600,'Semibold'],[700,'Bold'],[800,'Extra bold'],[900,'Black']]}],
  ['line_height','Line height',{t:'num',min:1,max:2.5,step:0.05}],
  ['emote_size','Emote size',{t:'num',min:12,max:120}],
  ['layout','Name placement',{t:'sel',opts:[['inline','Same line'],['stacked','Above message']]}]
];
var CHAT_LOOK = [
  ['text_color','Message colour',{t:'color'}],
  ['name_color_mode','Name colour',{t:'sel',opts:[['twitch','Viewer\'s own colour'],['custom','Always the same']]}],
  ['name_color','Name colour (fallback)',{t:'color'}],
  ['highlight_color','Accent',{t:'color'}],
  ['bubble','Message background',{t:'bool'}],
  ['bubble_color','Background colour',{t:'color'}],
  ['bubble_opacity','Background opacity',{t:'num',min:0,max:1,step:0.05}],
  ['bubble_radius','Corner radius',{t:'num',min:0,max:60}],
  ['padding','Padding',{t:'num',min:0,max:60}],
  ['gap','Space between messages',{t:'num',min:0,max:60}],
  ['outline','Outline text',{t:'bool'}],
  ['outline_color','Outline colour',{t:'color'}],
  ['outline_width','Outline width',{t:'num',min:0,max:8,step:0.5}],
  ['shadow','Drop shadow',{t:'bool'}]
];
var CHAT_BEHAVE = [
  ['max_messages','Messages on screen',{t:'num',min:1,max:100}],
  ['fade_after','Remove after (0 = keep)',{t:'num',min:0,max:600}],
  ['fade_duration','Fade out time',{t:'num',min:0,max:5,step:0.1}],
  ['direction','Newest message',{t:'sel',opts:[['bottom','At the bottom'],['top','At the top']]}],
  ['align','Alignment',{t:'sel',opts:[['left','Left'],['center','Centre'],['right','Right']]}],
  ['width_percent','Width of source',{t:'num',min:10,max:100}],
  ['animation','Entrance',{t:'sel',opts:[['slide','Slide'],['fade','Fade'],['pop','Pop'],['none','None']]}],
  ['show_badges','Show badges',{t:'bool'}],
  ['show_timestamps','Show timestamps',{t:'bool'}],
  ['highlight_first','Mark first-time chatters',{t:'bool'}]
];
var CHAT_FILTER = [
  ['third_party_emotes','7TV / BTTV / FFZ emotes',{t:'bool'}],
  ['hide_commands','Hide messages starting with !',{t:'bool'}],
  ['hide_users','Hide these accounts',{t:'text',ph:'nightbot, streamelements'}]
];
var EVENT_FIELDS = [
  ['font_family','Font',{t:'font'}],
  ['font_size','Main size',{t:'num',min:10,max:160}],
  ['sub_font_size','Label size',{t:'num',min:8,max:100}],
  ['text_color','Text colour',{t:'color'}],
  ['accent_color','Accent colour',{t:'color'}],
  ['bubble_color','Background colour',{t:'color'}],
  ['bubble_opacity','Background opacity',{t:'num',min:0,max:1,step:0.05}],
  ['bubble_radius','Corner radius',{t:'num',min:0,max:60}],
  ['align','Horizontal position',{t:'sel',opts:[['left','Left'],['center','Centre'],['right','Right']]}],
  ['valign','Vertical position',{t:'sel',opts:[['top','Top'],['middle','Middle'],['bottom','Bottom']]}],
  ['animation','Entrance',{t:'sel',opts:[['pop','Pop'],['slide','Slide'],['fade','Fade']]}],
  ['default_duration','Default seconds',{t:'num',min:1,max:60}],
  ['gap_between','Gap between alerts',{t:'num',min:0,max:10,step:0.1}],
  ['outline','Outline text',{t:'bool'}],
  ['outline_color','Outline colour',{t:'color'}],
  ['show_user_message','Show viewer message',{t:'bool'}]
];

/* ---------- field rendering ---------- */
function field(scope, key, label, spec){
  var id = 'f-' + scope + '-' + key;
  var val = CFG[scope][key];
  var el = document.createElement('label');
  el.className = spec.t === 'bool' ? 'f sw' : 'f';
  var input;

  if(spec.t === 'bool'){
    input = document.createElement('input'); input.type = 'checkbox'; input.checked = !!val;
    el.appendChild(input); el.appendChild(document.createTextNode(label));
  } else {
    el.appendChild(document.createTextNode(label));
    if(spec.t === 'sel'){
      input = document.createElement('select');
      spec.opts.forEach(function(o){
        var op = document.createElement('option');
        op.value = o[0]; op.textContent = o[1];
        if(String(o[0]) === String(val)) op.selected = true;
        input.appendChild(op);
      });
    } else if(spec.t === 'font'){
      input = document.createElement('input');
      input.setAttribute('list','fontlist'); input.value = val;
      if(!$('fontlist')){
        var dl = document.createElement('datalist'); dl.id = 'fontlist';
        FONTS.forEach(function(f){ var o=document.createElement('option'); o.value=f; dl.appendChild(o); });
        document.body.appendChild(dl);
      }
    } else {
      input = document.createElement('input');
      input.type = spec.t === 'num' ? 'number' : (spec.t === 'color' ? 'color' : 'text');
      if(spec.min !== undefined) input.min = spec.min;
      if(spec.max !== undefined) input.max = spec.max;
      if(spec.step !== undefined) input.step = spec.step;
      if(spec.ph) input.placeholder = spec.ph;
      input.value = val;
    }
    el.appendChild(input);
  }
  input.id = id;
  input.dataset.scope = scope; input.dataset.key = key; input.dataset.type = spec.t;
  return el;
}

function renderGroup(containerId, scope, defs){
  var c = $(containerId); c.innerHTML = '';
  defs.forEach(function(d){ c.appendChild(field(scope, d[0], d[1], d[2])); });
}

function collect(scope, defs){
  var out = {};
  defs.forEach(function(d){
    var el = $('f-' + scope + '-' + d[0]);
    if(!el) return;
    var t = d[2].t;
    if(t === 'bool') out[d[0]] = el.checked;
    else if(t === 'num') out[d[0]] = parseFloat(el.value);
    else out[d[0]] = el.value;
  });
  return out;
}

/* ---------- alerts table ---------- */
var ALERT_LABEL = {
  follow:'Follow', subscribe:'New sub', resub:'Resub', subgift:'Gift subs', cheer:'Bits',
  raid:'Raid', redeem:'Channel points', hypetrain:'Hype train', online:'Stream online', offline:'Stream offline'
};
function renderAlerts(){
  var tb = $('alerts-body'); tb.innerHTML = '';
  Object.keys(CFG.alerts).forEach(function(k){
    var a = CFG.alerts[k];
    var tr = document.createElement('tr');
    tr.innerHTML =
      '<td class="k">' + (ALERT_LABEL[k] || k) + '</td>' +
      '<td><input type="checkbox" id="a-' + k + '-on"' + (a.on ? ' checked' : '') + '></td>' +
      '<td><input type="number" min="1" max="60" id="a-' + k + '-duration" value="' + a.duration + '"></td>' +
      '<td><input id="a-' + k + '-title" value="' + esc(a.title) + '"></td>' +
      '<td><input id="a-' + k + '-body" value="' + esc(a.body) + '"></td>' +
      '<td><input id="a-' + k + '-clip" value="' + esc(a.clip || '') + '" placeholder="airhorn"></td>' +
      '<td><button class="sec" data-test="' + k + '">Test</button></td>';
    tb.appendChild(tr);
  });
}
function collectAlerts(){
  var out = {};
  Object.keys(CFG.alerts).forEach(function(k){
    out[k] = Object.assign({}, CFG.alerts[k], {
      on: $('a-'+k+'-on').checked,
      duration: parseFloat($('a-'+k+'-duration').value),
      title: $('a-'+k+'-title').value,
      body: $('a-'+k+'-body').value,
      clip: $('a-'+k+'-clip').value.trim()
    });
  });
  return out;
}

/* ---------- api ---------- */
async function saveConfig(patch, msg){
  var r = await fetch('/twitch/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(patch)
  });
  var d = await r.json();
  CFG = d.config;
  toast(msg || 'Saved');
}

async function loadAll(){
  CFG = await (await fetch('/twitch/api/config')).json();
  $('channel').value = CFG.channel || '';
  $('forward_url').value = CFG.forward_url || '';
  $('hexcast_url').value = CFG.hexcast_url || '';
  $('u-chat').textContent = location.origin + '/twitch/chat';
  $('u-events').textContent = location.origin + '/twitch/events';
  renderGroup('g-chat-type','chat',CHAT_TYPE);
  renderGroup('g-chat-look','chat',CHAT_LOOK);
  renderGroup('g-chat-behave','chat',CHAT_BEHAVE);
  renderGroup('g-chat-filter','chat',CHAT_FILTER);
  renderGroup('g-events','events',EVENT_FIELDS);
  renderAlerts();
}

function paintStatus(s){
  var c = $('st-conn'), src = $('st-src');
  c.textContent = s.connected ? 'connected' : 'not connected';
  c.className = 'pill ' + (s.connected ? 'on' : 'off');
  var label = s.source === 'eventsub' ? 'full access'
            : s.source === 'irc' ? 'chat only (anonymous)' : 'idle';
  src.textContent = label + (s.channel_login ? ' - #' + s.channel_login : '');
  src.className = 'pill';
  $('log').textContent = (s.log || []).join('\n');
  var html = '';
  (s.subs_ok || []).forEach(function(x){ html += '<div style="color:var(--good)">ok &nbsp;' + esc(x) + '</div>'; });
  (s.subs_failed || []).forEach(function(x){ html += '<div style="color:var(--warn)">-- &nbsp;' + esc(x) + '</div>'; });
  if(!html) html = 'Nothing subscribed yet. Sign in on the Connection tab.';
  $('subs').innerHTML = html;
}

function pushPreview(m){
  var p = $('preview');
  var d = document.createElement('div');
  d.className = 'm';
  d.innerHTML = '<b>' + esc(m.user.name) + '</b>: ' + esc(m.text);
  p.appendChild(d);
  while(p.children.length > 60) p.removeChild(p.firstChild);
  p.scrollTop = p.scrollHeight;
}

function connectPanel(){
  var proto = location.protocol === 'https:' ? 'wss' : 'ws';
  var ws = new WebSocket(proto + '://' + location.host + '/twitch/ws/panel');
  ws.onmessage = function(e){
    var m = JSON.parse(e.data);
    if(m.type === 'status') paintStatus(m.status);
    else if(m.type === 'chat_preview') pushPreview(m.message);
    else if(m.type === 'event') toast(m.event.title + ' - ' + m.event.body);
  };
  ws.onclose = function(){ setTimeout(connectPanel, 1500); };
}

/* ---------- wiring ---------- */
document.querySelectorAll('nav button').forEach(function(b){
  b.onclick = function(){
    document.querySelectorAll('nav button').forEach(function(x){ x.classList.remove('sel'); });
    document.querySelectorAll('section').forEach(function(x){ x.classList.remove('sel'); });
    b.classList.add('sel');
    $('tab-' + b.dataset.tab).classList.add('sel');
  };
});

document.addEventListener('click', function(e){
  var t = e.target;
  if(t.dataset && t.dataset.copy){
    navigator.clipboard.writeText($(t.dataset.copy).textContent);
    toast('Copied');
  }
  if(t.dataset && t.dataset.test){
    fetch('/twitch/api/test/event', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({kind: t.dataset.test})
    }).then(function(r){ if(!r.ok) toast('That alert is switched off'); });
  }
});

$('copy-redir').onclick = function(){
  navigator.clipboard.writeText($('redir').textContent); toast('Copied');
};
$('save-creds').onclick = async function(){
  await fetch('/twitch/api/credentials', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({client_id: $('client_id').value, client_secret: $('client_secret').value})
  });
  $('client_secret').value = '';
  toast('Credentials saved');
};
$('login').onclick = function(){ location.href = '/twitch/auth/login'; };
$('logout').onclick = async function(){ await fetch('/twitch/auth/logout', {method:'POST'}); toast('Signed out'); };
$('reconnect').onclick = async function(){ await fetch('/twitch/api/reconnect', {method:'POST'}); toast('Reconnecting'); };
$('save-conn').onclick = function(){
  saveConfig({channel: $('channel').value.trim().replace(/^#/,''),
              forward_url: $('forward_url').value.trim(),
              hexcast_url: $('hexcast_url').value.trim()});
};
$('save-chat').onclick = function(){
  saveConfig({chat: Object.assign({}, collect('chat',CHAT_TYPE), collect('chat',CHAT_LOOK),
                                  collect('chat',CHAT_BEHAVE), collect('chat',CHAT_FILTER))},
             'Chat overlay updated');
};
$('save-events').onclick = function(){
  saveConfig({events: collect('events',EVENT_FIELDS)}, 'Alert overlay updated');
};
$('save-alerts').onclick = function(){ saveConfig({alerts: collectAlerts()}, 'Alerts updated'); };
$('test-chat').onclick = function(){
  fetch('/twitch/api/test/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
};

loadAll().then(connectPanel);
setInterval(function(){
  fetch('/twitch/api/status').then(function(r){ return r.json(); }).then(paintStatus);
}, 5000);
</script>
</body></html>
"""
