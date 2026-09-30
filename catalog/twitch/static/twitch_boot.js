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
