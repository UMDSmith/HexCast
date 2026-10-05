/* Shared by the chat overlay, the alert overlay and (for fonts) the panel. Nothing here touches the page
   until a function is called, so it can be loaded anywhere. */

// A font name as it is written into CSS: no quotes, slashes or brackets that could end the string.
function cleanFont(name){
  return String(name == null ? '' : name).replace(/["'\\;{}<>()]/g, '').replace(/\s+/g, ' ').trim().slice(0, 80);
}
function fontVar(name){ var n = cleanFont(name); return n ? '"' + n + '"' : 'inherit'; }

// Fonts the streamer uploaded (/twitch/api/fonts): fetched once, and again after an upload or delete.
var _uploadedFonts = null;
function uploadedFonts(fresh){
  if(fresh || !_uploadedFonts){
    _uploadedFonts = fetch('/twitch/api/fonts').then(function(r){ return r.json(); })
      .then(function(d){ return d.fonts || []; }).catch(function(){ return []; });
  }
  return _uploadedFonts;
}
// The @font-face for an uploaded font. It claims every weight: a variable font then gets its whole range,
// and a single-weight file is used as it is instead of being faked bold.
function uploadedFaceCss(f){
  return '@font-face{font-family:"' + cleanFont(f.name) + '";src:url("' + f.url + '")' +
    (f.format ? ' format("' + f.format + '")' : '') + ';font-weight:100 900;font-display:swap}';
}
var _fontsAsked = {};
// Make a font usable: one the streamer uploaded is served by Hexcast; any other name is a Google font.
function loadFont(name){
  name = cleanFont(name);
  if(!name || _fontsAsked[name]) return;
  _fontsAsked[name] = true;
  uploadedFonts().then(function(list){
    var up = list.filter(function(f){ return f.name === name; })[0];
    var el;
    if(up){
      el = document.createElement('style');
      el.textContent = uploadedFaceCss(up);
    } else {
      el = document.createElement('link');
      el.rel = 'stylesheet';
      var fam = encodeURIComponent(name).replace(/%20/g, '+');
      el.href = 'https://fonts.googleapis.com/css2?family=' + fam + ':wght@400;500;600;700;800;900&display=swap';
      // a font with fewer weights, or none of those, is still fine asked for on its own
      el.onerror = function(){
        var again = document.createElement('link');
        again.rel = 'stylesheet';
        again.href = 'https://fonts.googleapis.com/css2?family=' + fam + '&display=swap';
        document.head.appendChild(again);
      };
    }
    document.head.appendChild(el);
  });
}
function connect(path, onMsg){
  var proto = location.protocol === 'https:' ? 'wss' : 'ws';
  // ?channel=name (one channel) or ?channel=all on the source's own URL picks what it shows; without it
  // the panel's "chat and alert sources show" setting decides
  var want = new URLSearchParams(location.search).get('channel');
  var url = path + (want ? (path.indexOf('?') < 0 ? '?' : '&') + 'channel=' + encodeURIComponent(want) : '');
  var ws = new WebSocket(proto + '://' + location.host + url);
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

// ---- background styles (see twitch_overlay.css) ----
var BG_FITS = {                               // bg_fit -> [background-size, background-repeat]
  cover:['cover','no-repeat'], contain:['contain','no-repeat'], stretch:['100% 100%','no-repeat'],
  tile:['auto','repeat'], center:['auto','no-repeat']
};
var BG_POSITIONS = ['center','top','bottom','left','right'];
function _num(v, d){ v = parseFloat(v); return isNaN(v) ? d : v; }
function cssUrl(u){ return u ? 'url("' + String(u).replace(/["\\]/g, '') + '")' : 'none'; }
// Set every --bg-* variable a "bgs bgs-<style>" element needs, from a chat/events config. `style` is an
// element's (or :root's) style object.
function bgVars(style, c){
  var op = _num(c.bubble_opacity, 1);
  var fit = BG_FITS[c.bg_fit] || BG_FITS.cover;
  var set = function(k, v){ style.setProperty(k, v); };
  set('--bg-color', hexToRgba(c.bubble_color, op));
  set('--bg-color2', hexToRgba(c.bg_color2 || c.bubble_color, op));
  set('--bg-angle', _num(c.bg_gradient_angle, 135) + 'deg');
  set('--bg-image', cssUrl(c.bg_image_url));
  set('--bg-size', fit[0]); set('--bg-repeat', fit[1]);
  set('--bg-pos', BG_POSITIONS.indexOf(c.bg_position) >= 0 ? c.bg_position : 'center');
  set('--bg-img-opacity', _num(c.bg_image_opacity, 1));
  set('--bg-dim', _num(c.bg_image_dim, 0));
  set('--bg-blur', _num(c.bg_blur, 0) + 'px');
  set('--bg-border', c.bg_border_color || '#ff3b30');
  set('--bg-border-w', _num(c.bg_border_width, 0) + 'px');
  set('--bg-slice', _num(c.bg_slice, 32));
  set('--bg-slice-w', _num(c.bg_slice_width, 24) + 'px');
  set('--bg-slice-repeat', c.bg_slice_repeat || 'stretch');
  set('--bg-pad', _num(c.bg_pad, 20) + 'px');
  set('--bg-radius', _num(c.bubble_radius, 14) + 'px');
  var sh = _num(c.bg_shadow, 0);
  set('--bg-filter', sh > 0 ? 'drop-shadow(0 ' + Math.round(sh / 2) + 'px ' + sh + 'px rgba(0,0,0,.55))' : 'none');
}
function bgClass(style){ return style && style !== 'none' ? 'bgs bgs-' + style : ''; }
// A frame with no picture picked would only draw a thick empty border, so it is drawn solid until one is chosen.
function bgStyleFor(style, url){
  style = style || 'solid';
  return style === 'slice' && !url ? 'solid' : style;
}

// The text-shadow of an overlay's `shadow` options.
function shadowCss(c){
  return _num(c.shadow_x, 0) + 'px ' + _num(c.shadow_y, 2) + 'px ' + _num(c.shadow_blur, 6) + 'px ' +
         hexToRgba(c.shadow_color || '#000000', _num(c.shadow_opacity, 0.85));
}

// The streamer's own CSS: added after the page's, as text (never as markup).
function applyCustomCss(css){
  var el = document.getElementById('custom-css');
  if(!el){ el = document.createElement('style'); el.id = 'custom-css'; document.head.appendChild(el); }
  el.textContent = String(css || '');
}

// ---- the panel's live preview ----
// The panel shows an overlay page in an iframe with ?preview=1. Such a page opens no socket: the panel
// sends it the config being edited and sample lines or alerts as postMessage, and it draws them exactly
// as the real overlay does.
function isPreview(){ return new URLSearchParams(location.search).get('preview') === '1'; }
function listenPreview(onMsg){
  window.addEventListener('message', function(e){
    var m = e.data;
    if(e.origin === location.origin && m && m.hexcast === 'preview') onMsg(m);
  });
  try { parent.postMessage({hexcast: 'preview-ready'}, location.origin); } catch(err){}
}
