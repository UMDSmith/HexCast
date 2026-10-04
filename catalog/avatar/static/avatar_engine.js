/* Hexcast Avatars - the renderer (window.HexAvatar).
 *
 * Draws every avatar on a 1920x1080 stage with PixiJS + untitled-pixi-live2d-engine (both part of
 * the Live2D runtime the Avatars tab downloads). The OBS overlay and the tab's live preview run this
 * same file, so the preview shows exactly what is on stream.
 *
 * Per frame, for every avatar (in the order the Cubism engine runs):
 *   motions (the model's own animations)                           - the engine
 *   -> the virtual tracker + mappings + expressions + API params   - here ("input" layer)
 *   -> physics, pose                                               - the engine
 *   -> API params with layer "final"                               - here
 * The virtual tracker is what keeps a model alive without a camera: blinking, breathing, idle
 * sway, wandering eyes, head motion while talking, gestures, gaze targets, emotions, lipsync. It
 * produces VTube Studio's tracking inputs (FaceAngleX, EyeOpenLeft, MouthSmile, VoiceA ...), and the
 * model's mappings (from its .vtube.json, or VTube Studio's defaults) turn them into parameters -
 * relative to each model's own neutral pose, so the same "smile" looks right on any rig. Any other
 * name a mapping takes as its input is a custom input (VTube Studio's custom parameters): nothing
 * in the tracker drives it, only the API's `params` does, through the same range mapping and smoothing.
 *
 * Idle motion is seeded by the avatar's name and the wall clock, so OBS and the preview (two pages,
 * one machine) move in step.
 */
(function () {
  'use strict';

  var W = 1920, H = 1080;
  var HA = window.HexAvatar = window.HexAvatar || {};
  var L = null;                                  // PIXI.live2d, once the runtime is there
  var PLUGIN_ADDED = false;

  // inputs that sit around a neutral value / are a factor of the neutral (eyes) / start at zero
  var CENTERED = { FaceAngleX: 1, FaceAngleY: 1, FaceAngleZ: 1, FacePositionX: 1, FacePositionY: 1, FacePositionZ: 1,
                   EyeLeftX: 1, EyeLeftY: 1, EyeRightX: 1, EyeRightY: 1, Brows: 1, BrowLeftY: 1, BrowRightY: 1,
                   MouthSmile: 1, MouthX: 1 };
  var FACTOR = { EyeOpenLeft: 1, EyeOpenRight: 1 };
  // the face stays the tracker's while an idle animation plays (and the mouth while talking, always)
  var FACE_KEEP = { EyeOpenLeft: 1, EyeOpenRight: 1, EyeLeftX: 1, EyeLeftY: 1, EyeRightX: 1, EyeRightY: 1, MouthSmile: 1 };
  var MOUTH_KEEP = { MouthOpen: 1, VoiceA: 1, VoiceI: 1, VoiceU: 1, VoiceE: 1, VoiceO: 1, VoiceSilence: 1, VoiceVolume: 1,
                     VoiceFrequency: 1, VoiceVolumePlusMouthOpen: 1, VoiceFrequencyPlusMouthSmile: 1, MouthSmile: 1 };
  var INPUTS = {};
  ['FaceAngleX', 'FaceAngleY', 'FaceAngleZ', 'FacePositionX', 'FacePositionY', 'FacePositionZ', 'EyeOpenLeft',
   'EyeOpenRight', 'EyeLeftX', 'EyeLeftY', 'EyeRightX', 'EyeRightY', 'Brows', 'BrowLeftY', 'BrowRightY', 'MouthSmile',
   'MouthOpen', 'MouthX', 'CheekPuff', 'TongueOut', 'VoiceA', 'VoiceI', 'VoiceU', 'VoiceE', 'VoiceO', 'VoiceSilence',
   'VoiceVolume', 'VoiceFrequency', 'VoiceVolumePlusMouthOpen', 'VoiceFrequencyPlusMouthSmile'].forEach(function (k) { INPUTS[k] = 1; });

  /* ------------------------------------------------------------------ utils */

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function nowSec() { return (performance.timeOrigin + performance.now()) / 1000; }
  function approach(cur, target, dt, tau) { return tau <= 0 ? target : cur + (target - cur) * (1 - Math.exp(-dt / tau)); }
  function ease(name, t) {
    t = clamp(t, 0, 1);
    switch (name) {
      case 'linear': return t;
      case 'in': return t * t * t;
      case 'out': return 1 - Math.pow(1 - t, 3);
      case 'inout': return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      case 'back': var c = 1.70158; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
      case 'bounce': var n = 7.5625, d = 2.75;
        if (t < 1 / d) return n * t * t;
        if (t < 2 / d) return n * (t -= 1.5 / d) * t + 0.75;
        if (t < 2.5 / d) return n * (t -= 2.25 / d) * t + 0.9375;
        return n * (t -= 2.625 / d) * t + 0.984375;
      default: return t * t * (3 - 2 * t);                       // smooth
    }
  }
  function hashStr(s) { var h = 2166136261; for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  function hash(a, b) {                                            // -> [0, 1)
    var h = Math.imul((a | 0) ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul((b | 0) + 0x632be5ab, 0xc2b2ae35);
    h ^= h >>> 15; h = Math.imul(h, 0x2c1b3c6d); h ^= h >>> 12; h = Math.imul(h, 0x297a2d39); h ^= h >>> 15;
    return (h >>> 0) / 4294967296;
  }
  function noise(seed, t) {                                        // smooth value noise, -1..1
    var i = Math.floor(t), f = t - i, u = f * f * (3 - 2 * f);
    return lerp(hash(seed, i), hash(seed, i + 1), u) * 2 - 1;
  }
  function fbm(seed, t) { return noise(seed, t) * 0.68 + noise(seed + 101, t * 2.13) * 0.32; }
  function hexRgb(h) { var n = parseInt(String(h || '#ffffff').slice(1), 16) || 0; return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255]; }
  function hsv(h, s, v) {
    var i = Math.floor(h * 6), f = h * 6 - i, p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
    switch (i % 6) { case 0: return [v, t, p]; case 1: return [q, v, p]; case 2: return [p, v, t]; case 3: return [p, q, v]; case 4: return [t, p, v]; default: return [v, p, q]; }
  }
  function resolveUrl(rel, base) { try { return new URL(rel, new URL(base, location.href)).href; } catch (e) { return rel; } }
  var jsonCache = {};
  function getJSON(url) {
    if (!jsonCache[url]) jsonCache[url] = fetch(url, { cache: 'no-cache' }).then(function (r) { if (!r.ok) throw new Error(url + ' ' + r.status); return r.json(); })
      .catch(function (e) { delete jsonCache[url]; throw e; });
    return jsonCache[url];
  }

  /* ------------------------------------------------------------------ the stage */

  function Stage(opts) {
    this.opts = opts || {};
    this.role = this.opts.role || 'obs';
    this.mount = this.opts.mount;
    this.audioOn = this.opts.audio !== false;
    this.only = this.opts.only || null;              // [names] or null = every avatar
    this.overlayId = this.opts.overlay == null ? null : this.opts.overlay;   // the overlay this stage draws; null = every overlay (`this.overlay` is the selection graphics)
    this.avatars = {};
    this.order = [];
    this.config = { avatars: [], stage: {} };
    this.models = {};
    this.items = {};
    this.live = {};
    this.listeners = {};
    this.selected = null;
    this.editable = !!this.opts.editable;
    this._ft = { n: 0, ms: 0, t0: performance.now() };
    this._audio = null;
    this.pending = [];
  }
  HA.Stage = Stage;

  Stage.prototype.on = function (ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); };
  Stage.prototype.emit = function (ev, data) { (this.listeners[ev] || []).forEach(function (fn) { try { fn(data); } catch (e) { console.error(e); } }); };

  Stage.prototype.ready = function () { return !!(window.PIXI && PIXI.live2d && window.Live2DCubismCore); };

  Stage.prototype.init = async function () {
    if (!this.ready()) throw new Error('the Live2D runtime is not installed');
    L = PIXI.live2d;
    if (!PLUGIN_ADDED) {
      PIXI.extensions.add(L.Live2DPlugin);
      try { L.configureCubismSDK({ memorySizeMB: 160 }); } catch (e) {}
      PLUGIN_ADDED = true;
    }
    (HA.stages = HA.stages || []).push(this);       // for the browser console: HexAvatar.stages[0].avatars
    var app = this.app = new PIXI.Application();
    await app.init({ resizeTo: this.mount, backgroundAlpha: 0, preference: 'webgl', antialias: true, autoDensity: true,
                     resolution: Math.min(2, window.devicePixelRatio || 1), powerPreference: 'high-performance' });
    this.mount.appendChild(app.canvas);
    app.canvas.style.display = 'block';
    this.root = new PIXI.Container();
    app.stage.addChild(this.root);
    this.overlay = new PIXI.Graphics();            // selection outline etc. (preview only)
    app.stage.addChild(this.overlay);
    var self = this;
    this._resize = function () { self.fit(); };
    window.addEventListener('resize', this._resize);
    if (window.ResizeObserver) { this._ro = new ResizeObserver(this._resize); this._ro.observe(this.mount); }
    this.fit();
    app.ticker.add(this._start, this, PIXI.UPDATE_PRIORITY.HIGH);
    app.ticker.add(this.tick, this, PIXI.UPDATE_PRIORITY.NORMAL);
    app.ticker.add(this._end, this, PIXI.UPDATE_PRIORITY.UTILITY);
    if (this.editable) this._editor();
    var p = this.pending; this.pending = [];
    p.forEach(function (fn) { fn(); });
  };

  /* the 1920x1080 stage, fitted (letterboxed) into the page */
  Stage.prototype.fit = function () {
    if (!this.app) return;
    var w = this.app.screen.width, h = this.app.screen.height, s = Math.min(w / W, h / H);
    this.scale = s;
    this.root.scale.set(s);
    this.root.position.set((w - W * s) / 2, (h - H * s) / 2);
  };

  Stage.prototype.audio = function () {
    if (!this._audio) {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      this._audio = new Ctx();
    }
    if (this._audio.state === 'suspended') this._audio.resume().catch(function () {});
    return this._audio;
  };

  /* does this stage draw the avatar (its config entry)? the ?avatar= filter, then the avatar's overlay (`main` if it has none) */
  Stage.prototype.shown = function (a) {
    if (this.only && this.only.indexOf(a.name) < 0) return false;
    return this.overlayId == null || (a.overlay || 'main') === this.overlayId;
  };

  /* the tab's preview draws one overlay at a time: switch it (the avatars of the others go, the new ones come) */
  Stage.prototype.setOverlay = function (id) {
    if (this.overlayId === id) return;
    this.overlayId = id;
    this.setConfig(this.config);
  };

  /* everything from the server: config, models, items, live state */
  Stage.prototype.snapshot = function (s) {
    this.models = s.models || {};
    this.items = {};
    var self = this;
    (s.items || []).forEach(function (it) { self.items[it.id] = it; });
    this.live = s.live || {};
    this.setConfig(s.config);
  };

  Stage.prototype.setLibrary = function (models, items) {
    this.models = models || {};
    var self = this;
    this.items = {};
    (items || []).forEach(function (it) { self.items[it.id] = it; });
    for (var n in this.avatars) this.avatars[n].libraryChanged();
  };

  /* `live` (the config message carries it): the state an avatar that appears now - created, or moved here from
     another overlay - starts in. Without it the state from the snapshot is kept. */
  Stage.prototype.setConfig = function (cfg, live) {
    if (!this.app) { var self0 = this; this.pending.push(function () { self0.setConfig(cfg, live); }); return; }
    if (live) this.live = live;
    this.config = cfg || { avatars: [] };
    var st = this.config.stage || {};
    this.app.ticker.maxFPS = st.fps || 60;
    var keep = {}, self = this;
    (this.config.avatars || []).forEach(function (a) {
      if (!self.shown(a)) return;
      keep[a.name] = 1;
      var av = self.avatars[a.name];
      if (!av) {
        av = self.avatars[a.name] = new Avatar(self, a);
        var live = self.live[a.name];
        if (live) av.restore(live);
      }
      av.setConfig(a);
    });
    for (var n in this.avatars) if (!keep[n]) { this.avatars[n].destroy(); delete this.avatars[n]; }
    // draw order: the config's list order, last on top
    this.order = (this.config.avatars || []).map(function (a) { return a.name; }).filter(function (n) { return keep[n]; });
    this.order.forEach(function (n, i) { self.avatars[n].node.zIndex = i; });
    this.root.sortableChildren = true;
    if (this.selected && !this.avatars[this.selected]) this.select(null);
  };

  Stage.prototype.command = function (msg) {
    var av = this.avatars[msg.avatar];
    if (av) av.command(msg);
  };

  Stage.prototype.pcm = function (buf) {
    var u8 = new Uint8Array(buf), klen = u8[0];
    var key = new TextDecoder().decode(u8.subarray(1, 1 + klen));
    var start = 1 + klen, samples = new Int16Array(buf.slice(start, start + ((buf.byteLength - start) >> 1) * 2));
    for (var n in this.avatars) {
      var av = this.avatars[n], m = av.cfg.mouth || {};
      if (m.source === 'device' && m.device === key) av.lip.push(samples, 16000);
    }
  };

  Stage.prototype._start = function () { this._t0 = performance.now(); };
  Stage.prototype._end = function () {
    var ft = this._ft;
    ft.ms += performance.now() - this._t0;
    ft.n++;
    var now = performance.now();
    if (now - ft.t0 > 2000) {
      var per = {};
      for (var n in this.avatars) { var a = this.avatars[n]; per[n] = { ms: +(a.msAcc / Math.max(1, a.msN)).toFixed(2), visible: a.node.visible, loaded: !!a.model }; a.msAcc = a.msN = 0; }
      this.emit('stats', { fps: Math.round(ft.n * 1000 / (now - ft.t0)), frame_ms: +(ft.ms / Math.max(1, ft.n)).toFixed(2), avatars: per,
                           w: Math.round(this.app.screen.width), h: Math.round(this.app.screen.height) });
      ft.n = 0; ft.ms = 0; ft.t0 = now;
    }
  };

  Stage.prototype.tick = function (ticker) {
    var dt = Math.min(0.1, ticker.deltaMS / 1000), now = nowSec();
    for (var i = 0; i < this.order.length; i++) {
      var av = this.avatars[this.order[i]];
      if (av) av.update(dt, now);
    }
    if (this.editable) this._drawSelection();
  };

  Stage.prototype.destroy = function () {
    for (var n in this.avatars) this.avatars[n].destroy();
    this.avatars = {};
    window.removeEventListener('resize', this._resize);
    if (this._ro) this._ro.disconnect();
    if (this.app) this.app.destroy(true, { children: true });
  };

  /* stage <-> page coordinates */
  Stage.prototype.toStage = function (clientX, clientY) {
    var r = this.app.canvas.getBoundingClientRect();
    var gx = (clientX - r.left) * (this.app.screen.width / r.width), gy = (clientY - r.top) * (this.app.screen.height / r.height);
    return { x: (gx - this.root.position.x) / this.scale, y: (gy - this.root.position.y) / this.scale, gx: gx, gy: gy };
  };

  /* ------------------------------------------------------------------ preview editing */

  Stage.prototype.select = function (name) {
    if (this.selected === name) return;
    this.selected = name;
    this.emit('select', name);
  };

  /* an item under a page point: front items first (they are drawn over the model); back items
     only where no model is in the way */
  Stage.prototype.pickItem = function (gx, gy) {
    var self = this;
    function scan(layer) {
      for (var i = self.order.length - 1; i >= 0; i--) {
        var av = self.avatars[self.order[i]];
        if (!av || !av.node.visible) continue;
        for (var j = av.items.length - 1; j >= 0; j--) {
          var it = av.items[j];
          if (!it.sprite || !it.node.visible || it.cfg.layer !== layer) continue;
          var b = it.sprite.getBounds();
          if (gx >= b.x && gy >= b.y && gx <= b.x + b.width && gy <= b.y + b.height) return { av: av, it: it };
        }
      }
      return null;
    }
    return scan('front') || (this.pick(gx, gy) ? null : scan('back'));
  };

  Stage.prototype.pick = function (gx, gy) {
    for (var i = this.order.length - 1; i >= 0; i--) {
      var av = this.avatars[this.order[i]];
      if (av && av.node.visible && av.model && av.hit(gx, gy)) return av.name;
    }
    return null;
  };

  Stage.prototype._drawSelection = function () {
    var g = this.overlay;
    g.clear();
    var av = this.selected && this.avatars[this.selected];
    if (!av || !av.model || !av.node.visible) { this._handle = null; return; }
    var b = av.model.getBounds();
    var col = av.cfg.locked ? 0x8a8a9c : 0xff5fa2;
    g.rect(b.x, b.y, b.width, b.height).stroke({ width: 2, color: col, alpha: 0.9 });
    // the rotate handle: a dot above the box
    var hx = b.x + b.width / 2, hy = b.y - 26;
    if (!av.cfg.locked) {
      g.moveTo(hx, b.y).lineTo(hx, hy + 8).stroke({ width: 2, color: col, alpha: 0.8 });
      g.circle(hx, hy, 8).fill({ color: 0xffffff }).stroke({ width: 2, color: col });
      this._handle = { x: hx, y: hy, cx: b.x + b.width / 2, cy: b.y + b.height / 2 };
    } else this._handle = null;
  };

  Stage.prototype._editor = function () {
    var self = this, cv = this.app.canvas, drag = null;
    cv.style.touchAction = 'none';
    function transformOf(av) { return { x: av.tr.cur.x, y: av.tr.cur.y, scale: av.tr.cur.scale, rotation: av.tr.cur.rotation }; }
    cv.addEventListener('pointerdown', function (e) {
      self.audio();
      var p = self.toStage(e.clientX, e.clientY);
      var h = self._handle;
      if (h && Math.hypot(p.gx - h.x, p.gy - h.y) < 14 && self.selected) {
        var av0 = self.avatars[self.selected];
        drag = { kind: 'rotate', av: av0, cx: h.cx, cy: h.cy, start: transformOf(av0),
                 a0: Math.atan2(p.gy - h.cy, p.gx - h.cx) };
      } else {
        var hitItem = self.pickItem(p.gx, p.gy);
        if (hitItem && !hitItem.av.cfg.locked) {
          self.select(hitItem.av.name);
          var it0 = hitItem.it, lp0 = hitItem.av.node.toLocal(new PIXI.Point(p.gx, p.gy));
          drag = { kind: 'item', av: hitItem.av, it: it0, pinned: !!it0.cfg.pin, dx: it0.node.position.x - lp0.x, dy: it0.node.position.y - lp0.y };
          it0.cfg = Object.assign({}, it0.cfg, { pin: null });            // follow the pointer while dragged
          self.emit('item', { avatar: hitItem.av.name, id: it0.cfg.id, select: true });
          cv.setPointerCapture(e.pointerId);
          e.preventDefault();
          return;
        }
        var name = self.pick(p.gx, p.gy);
        self.select(name);
        if (!name) return;
        var av = self.avatars[name];
        if (av.cfg.locked) return;
        drag = { kind: 'move', av: av, sx: p.x, sy: p.y, start: transformOf(av) };
      }
      cv.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    cv.addEventListener('pointermove', function (e) {
      if (!drag) { var p0 = self.toStage(e.clientX, e.clientY), hh = self._handle;
        cv.style.cursor = hh && Math.hypot(p0.gx - hh.x, p0.gy - hh.y) < 14 ? 'grab' : (self.pick(p0.gx, p0.gy) ? 'move' : 'default'); return; }
      var p = self.toStage(e.clientX, e.clientY), t = {};
      if (drag.kind === 'item') {
        var pos = drag.av.itemPos(drag.it, p.gx, p.gy, drag.dx, drag.dy);
        drag.it.cfg.x = pos.x; drag.it.cfg.y = pos.y;
        drag.it._place();
        self.emit('item', { avatar: drag.av.name, id: drag.it.cfg.id, patch: { x: pos.x, y: pos.y }, final: false });
        return;
      }
      if (drag.kind === 'move') {
        t.x = drag.start.x + (p.x - drag.sx) / W * 100;
        t.y = drag.start.y + (p.y - drag.sy) / H * 100;
        if (e.shiftKey) { t.x = Math.round(t.x); t.y = Math.round(t.y); }
      } else {
        var a = Math.atan2(p.gy - drag.cy, p.gx - drag.cx);
        var deg = drag.start.rotation + (a - drag.a0) * 180 / Math.PI;
        if (e.shiftKey) deg = Math.round(deg / 15) * 15;
        else if (Math.abs(deg) < 2.5) deg = 0;
        t.rotation = Math.round(deg * 10) / 10;
      }
      drag.av.setTransformNow(t);
      self.emit('transform', { name: drag.av.name, t: t, final: false });
    });
    function up(e) {
      if (!drag) return;
      var av = drag.av, d = drag;
      drag = null;
      try { cv.releasePointerCapture(e.pointerId); } catch (x) {}
      if (d.kind === 'item') {
        var patch = { x: d.it.cfg.x, y: d.it.cfg.y };
        if (d.pinned) patch.pin = av.pinAt(d.it) || null;             // a pinned item sticks where it is dropped
        self.emit('item', { avatar: av.name, id: d.it.cfg.id, patch: patch, final: true });
        return;
      }
      self.emit('transform', { name: av.name, t: transformOf(av), final: true });
    }
    cv.addEventListener('pointerup', up);
    cv.addEventListener('pointercancel', up);
    var wheelTimer = null;
    var itemWheel = null;
    cv.addEventListener('wheel', function (e) {
      var p = self.toStage(e.clientX, e.clientY);
      var hi = self.pickItem(p.gx, p.gy);
      if (hi && !hi.av.cfg.locked) {                                  // resize an item
        e.preventDefault();
        var sc = clamp((hi.it.cfg.scale || 1) * Math.exp(-e.deltaY * 0.0012), 0.03, 20);
        hi.it.setConfig(Object.assign({}, hi.it.cfg, { scale: Math.round(sc * 1000) / 1000 }));
        clearTimeout(itemWheel);
        itemWheel = setTimeout(function () { self.emit('item', { avatar: hi.av.name, id: hi.it.cfg.id, patch: { scale: hi.it.cfg.scale }, final: true }); }, 300);
        return;
      }
      var name = self.selected && self.avatars[self.selected] ? self.selected : self.pick(p.gx, p.gy);
      if (!name) return;
      var av = self.avatars[name];
      if (av.cfg.locked) return;
      e.preventDefault();
      self.select(name);
      var cur = transformOf(av), k = Math.exp(-e.deltaY * (e.ctrlKey ? 0.004 : 0.0012));
      var s = clamp(cur.scale * k, 0.05, 12), f = s / cur.scale;
      // zoom around the cursor: the point under it stays put
      var px = p.x / W * 100, py = p.y / H * 100;
      var t = { scale: Math.round(s * 1000) / 1000, x: px + (cur.x - px) * f, y: py + (cur.y - py) * f };
      av.setTransformNow(t);
      self.emit('transform', { name: name, t: t, final: false });
      clearTimeout(wheelTimer);
      wheelTimer = setTimeout(function () { self.emit('transform', { name: name, t: transformOf(av), final: true }); }, 350);
    }, { passive: false });
    cv.addEventListener('dblclick', function (e) {
      var p = self.toStage(e.clientX, e.clientY), name = self.pick(p.gx, p.gy);
      if (!name || self.avatars[name].cfg.locked) return;
      self.emit('reset', name);
    });
  };

  /* ------------------------------------------------------------------ one avatar */

  function emptyLive() { return { params: {}, expressions: {}, emotion: null, face: null, look: null, light: null, transform: null, visible: null }; }

  function Avatar(stage, cfg) {
    this.stage = stage;
    this.name = cfg.name;
    this.seed = hashStr(cfg.name);
    this.cfg = cfg;
    this.node = new PIXI.Container();
    this.node.label = 'avatar:' + cfg.name;
    this.back = new PIXI.Container();
    this.holder = new PIXI.Container();
    this.front = new PIXI.Container();
    this.back.sortableChildren = this.front.sortableChildren = true;
    this.node.addChild(this.back, this.holder, this.front);
    stage.root.addChild(this.node);
    this.model = null;
    this.modelId = '';
    this.loadSeq = 0;
    this.tr = { cur: { x: cfg.x, y: cfg.y, scale: cfg.scale, rotation: cfg.rotation }, from: null, to: null, t0: 0, dur: 0, ease: 'smooth', flip: !!cfg.flip, override: null };
    this.alpha = { cur: cfg.visible ? 1 : 0, to: cfg.visible ? 1 : 0, rate: 0.4, override: null };
    this.node.alpha = this.alpha.cur;
    this.node.visible = this.alpha.cur > 0.001;
    this.lip = new window.HexLipsync.Analyzer();
    this.speech = { queue: [], cur: null };
    this.ov = {};                // param / input overrides
    this.inputs = Object.create(null);   // the input names the model's mappings take (set by _setInputs)
    this.partOv = {};            // part opacity overrides (the `parts` command)
    this.exprs = {};             // active expressions
    this.emo = { name: 'neutral', face: {}, intensity: 1, until: 0, tau: 0.4 };
    this.apiFace = { face: {}, until: 0, tau: 0.25 };
    this.faceNow = { smile: 0, brows: 0, eyes: 1, mouth_open: 0, cheek: 0, mouth_x: 0, tilt: 0, wink_left: 0, wink_right: 0 };
    this.look = null;
    this.gaze = { ex: 0, ey: 0, hx: 0, hy: 0, tx: 0, ty: 0 };
    this.gestures = [];
    this.own = {};               // param index -> {w, to}: parameters the playing motion animates
    this.motionKey = '';
    this.frame = null;
    this.acc = 0;
    this.msAcc = 0; this.msN = 0;
    this.items = [];
    this.itemSig = '';
    this.lightCfg = null;
    this.light = null;
    this.lightW = 0;
    this.env = { fast: 0, slow: 0 };
  }

  Avatar.prototype.destroy = function () {
    this.stopSpeaking(true);
    this._clearItems();
    if (this.model) { try { this.model.destroy(); } catch (e) {} }
    this.model = null;
    this.node.destroy({ children: true });
  };

  Avatar.prototype.libraryChanged = function () {
    var meta = this.stage.models[this.cfg.model];
    if (this.cfg.model && (!this.model || this.modelId !== this.cfg.model) && meta && meta.ok) this.load(this.cfg.model);
    this.itemSig = '';
    this._syncItems();
  };

  Avatar.prototype.setConfig = function (cfg) {
    var prev = this.cfg;
    this.cfg = cfg;
    this.name = cfg.name;
    this.lip.configure(cfg.mouth || {});
    // placement: the saved one unless the API moved the avatar for now
    if (!this.tr.override) this._tweenTo({ x: cfg.x, y: cfg.y, scale: cfg.scale, rotation: cfg.rotation }, 0, 'smooth');
    this.tr.flip = this.tr.override && this.tr.override.flip != null ? this.tr.override.flip : !!cfg.flip;
    if (this.alpha.override == null) this._fadeTo(cfg.visible ? 1 : 0, 0.3);
    if (cfg.model !== this.modelId || !this.model) {
      if (cfg.model) this.load(cfg.model);
      else this.unload();
    } else if (prev && prev.idle && cfg.idle && prev.idle.idle_motion !== cfg.idle.idle_motion) {
      this.load(cfg.model);                              // the idle group is chosen when the model loads
    } else if (prev && prev.idle && cfg.idle && prev.idle.physics !== cfg.idle.physics) {
      this._applyIdleSettings();
    }
    if (!this.lightOverride) this.setLight(cfg.light, 0.5);
    this._syncItems();
    if (this.speech.cur && cfg.mouth && cfg.mouth.lipsync === false) this.lip.attachAnalyser(null);
  };

  Avatar.prototype.unload = function () {
    this.loadSeq++;
    this.png = null;
    if (this.model) { try { this.model.destroy(); } catch (e) {} }
    this.model = null;
    this.modelId = '';
    this._showProblem(this.cfg.model ? 'model missing' : 'no model chosen');
  };

  Avatar.prototype._showProblem = function (text) {
    if (this._problem) { this._problem.destroy(); this._problem = null; }
    if (!text || this.stage.role === 'obs') return;     // nothing ever shows on stream
    var t = new PIXI.Text({ text: this.name + ': ' + text, style: { fill: '#ffb4d4', fontSize: 34, fontFamily: 'Segoe UI, sans-serif', fontWeight: '600' } });
    t.anchor.set(0.5);
    this._problem = t;
    this.holder.addChild(t);
  };

  Avatar.prototype.load = async function (mid) {
    var seq = ++this.loadSeq, self = this;
    var meta = this.stage.models[mid];
    if (!meta) { this._showProblem('model "' + mid + '" is not in the library'); return; }
    if (!meta.ok) { this._showProblem(meta.problem || 'this model cannot be drawn'); return; }
    this._showProblem('loading...');
    var spec, mset;
    try {
      var both = await Promise.all([
        fetch('/avatar/api/models/' + encodeURIComponent(mid) + '/engine', { cache: 'no-store' }).then(function (r) { return r.json(); }),
        fetch('/avatar/api/models/' + encodeURIComponent(mid), { cache: 'no-store' }).then(function (r) { return r.json(); })
      ]);
      spec = both[0]; mset = both[1];
      if (spec.error) throw new Error(spec.error);
    } catch (e) { if (seq === this.loadSeq) this._showProblem('could not read the model: ' + e.message); return; }
    if (seq !== this.loadSeq) return;
    if (spec.type === 'png') return this._loadPng(seq, mid, meta, spec);
    var groups = (spec.FileReferences || {}).Motions || {};
    var idleGroup = '__hexcast_no_idle__';
    if (this.cfg.idle.idle_motion) {
      if (mset.settings && mset.settings.idle_motion) {
        var im0 = (meta.motions || []).filter(function (m) { return m.file === mset.settings.idle_motion || m.name === mset.settings.idle_motion.replace(/\.motion3\.json$/i, ''); })[0];
        if (im0) idleGroup = im0.group;
      } else if (groups.Idle) idleGroup = 'Idle';
      else if (groups.idle) idleGroup = 'idle';
    }
    var m;
    try {
      m = await L.Live2DModel.from(spec, { autoUpdate: false, autoFocus: false, autoHitTest: false, idleMotionGroup: idleGroup,
                                           motionPreload: 'NONE', eyeBlink: false });
    } catch (e) {
      if (seq === this.loadSeq) this._showProblem('could not load: ' + (e && e.message || e));
      this.stage.emit('event', { event: 'error', avatar: this.name, message: 'model load failed: ' + (e && e.message || e) });
      return;
    }
    if (seq !== this.loadSeq) { try { m.destroy(); } catch (e) {} return; }
    if (this.model) { try { this.model.destroy(); } catch (e) {} }
    this._showProblem(null);
    this.kind = 'live2d';
    this.png = null;
    this.model = m;
    this.modelId = mid;
    this.meta = meta;
    this.spec = spec;
    this.mset = mset.settings || {};
    var im = m.internalModel;
    im.eyeBlink = null;                                  // blinking and breathing are the tracker's
    im.breath = null;
    this._physics = im.physics || null;
    m.anchor.set(0.5, 0.5);
    this.baseScale = H / (im.originalHeight || m.height || H);
    m.scale.set(this.baseScale);
    this.holder.addChildAt(m, 0);
    this._table(im);
    this._compile();
    this._applyIdleSettings();
    this.partCurves = [];
    this.restPartOpacity = null;
    this.motionKey = '';
    this._restParts(idleGroup);
    var cm = im.coreModel;
    // what this avatar costs: the engine updates (motions, physics, Cubism's deformation) and draws
    // the model inside its render call, so that is what is timed
    var render = m.renderLive2D;
    if (typeof render === 'function') m.renderLive2D = function (r) { var t = performance.now(); render.call(this, r); self.msAcc += performance.now() - t; };
    im.on('afterMotionUpdate', function () { self._inputLayer(cm); });
    im.on('beforeModelUpdate', function () { self._finalLayer(cm); });
    // live state that arrived before the model
    for (var k in this.exprs) this._loadExpr(k);
    this._reportInfo();
    this._syncItems();
    this.stage.emit('loaded', { name: this.name, model: mid });
    this.stage.emit('event', { event: 'loaded', avatar: this.name, model: mid });
  };

  /* a PNGtuber: the rig takes the place of the Live2D model; everything around it stays the same */
  Avatar.prototype._loadPng = async function (seq, mid, meta, spec) {
    var rig = new PngRig(this, spec);
    try { await rig.load(spec.base); } catch (e) {
      if (seq === this.loadSeq) this._showProblem('could not load: ' + (e && e.message || e));
      this.stage.emit('event', { event: 'error', avatar: this.name, message: 'PNGtuber load failed: ' + (e && e.message || e) });
      return;
    }
    if (seq !== this.loadSeq) { rig.destroy(); return; }
    if (this.model) { try { this.model.destroy(); } catch (e) {} }
    this._showProblem(null);
    this.kind = 'png';
    this.png = rig;
    this.model = rig.root;
    this.modelId = mid;
    this.meta = meta;
    this.spec = spec;
    this.mset = {};
    this.P = { ids: [], index: Object.create(null), min: [], max: [], def: [] };
    this.maps = [];
    this._setInputs([]);
    this.Parts = rig.parts;
    this.baseScale = H / rig.box.h;
    rig.root.scale.set(this.baseScale);
    this.holder.addChildAt(rig.root, 0);
    // a PNGtuber's expressions are its states: the last one switched on is shown
    var on = Object.keys(this.exprs).filter(function (k) { return this.exprs[k].to > 0; }, this);
    rig.setState(on.length ? on[on.length - 1] : null);
    this.exprs = {};
    this.info = { parameters: [], parts: rig.layers.map(function (l) { return { id: l.id, name: l.name || '' }; }), drawables: [],
                  hit_areas: [], size: [Math.round(rig.box.w), Math.round(rig.box.h)], states: (meta.states || []) };
    this.stage.emit('model_info', { model: mid, info: this.info });
    this._syncItems();
    this.stage.emit('loaded', { name: this.name, model: mid });
    this.stage.emit('event', { event: 'loaded', avatar: this.name, model: mid });
  };

  /* the PNGtuber editor's unsaved rig, shown right away (the preview only) */
  Avatar.prototype.previewRig = function (rig) {
    if (this.kind !== 'png' || !this.spec) return;
    var seq = ++this.loadSeq, state = this.png && this.png.state;
    var self = this;
    this._loadPng(seq, this.modelId, this.meta, Object.assign({}, rig, { type: 'png', base: this.spec.base, url: this.spec.url }))
      .then(function () { if (self.png && state) self.png.setState(state); });
  };

  /* a tracker input as the API holds it (VTube Studio units), over what the tracker made of it */
  Avatar.prototype.inputOv = function (name, base) {
    var o = this.ov[name];
    if (!o || !o.isInput || o.w <= 0) return base;
    var w = (o.weight == null ? 1 : o.weight) * o.w;
    return o.mode === 'add' ? base + o.value * w : lerp(base, o.value, w);
  };

  /* Is a `params` key a tracker input, or a Live2D parameter to override? An input is one of VTube
     Studio's standard names or the input of any of the model's mappings (a custom input) - and a
     mapping wins over a parameter of the same id, as in VTube Studio. This is decided where the
     value is applied, not once when the command arrives: a held command can arrive before the model
     (and so its mappings) has loaded, and the model can change. */
  Avatar.prototype._classify = function (k, o) {
    var inp = Object.prototype.hasOwnProperty.call(INPUTS, k) || this.inputs[k] === 1;
    if (o.isInput === inp) return;
    o.isInput = inp;
    o.rateIn = o.duration > 0 && !o.restore ? (inp ? o.duration : 0.0001) : 0.0001;
    o.dur = inp || o.restore ? 0 : o.duration;
    if (inp) o.from = null;
    else if (o.from == null && this.model && this.P && this.P.index[k] !== undefined) {
      o.from = this.model.internalModel.coreModel.getParameterValueByIndex(this.P.index[k]);
      o.started = performance.now();
    }
  };

  Avatar.prototype._setInputs = function (names) {
    var set = Object.create(null);
    for (var i = 0; i < names.length; i++) set[names[i]] = 1;
    this.inputs = set;
    for (var k in this.ov) this._classify(k, this.ov[k]);       // overrides that arrived earlier
  };

  /* the model's box width in avatar units (items are placed in % of it) */
  Avatar.prototype.boxWidth = function () {
    if (this.png) return this.png.box.w * this.baseScale;
    var im = this.model && this.model.internalModel;
    return im ? im.originalWidth * this.baseScale : H * 0.6;
  };

  Avatar.prototype._applyIdleSettings = function () {
    if (!this.model || this.png) return;
    var im = this.model.internalModel;
    im.physics = this.cfg.idle.physics ? this._physics : null;
  };

  /* every parameter of the model: id <-> index, range, default */
  Avatar.prototype._table = function (im) {
    var cm = im.coreModel, n = cm.getParameterCount(), P = { ids: [], index: Object.create(null), min: [], max: [], def: [] };   // (no prototype: a key like "constructor" is no parameter)
    for (var i = 0; i < n; i++) {
      var id = cm.getParameterId(i).getString().s;
      P.ids.push(id); P.index[id] = i;
      P.min.push(cm.getParameterMinimumValue(i)); P.max.push(cm.getParameterMaximumValue(i)); P.def.push(cm.getParameterDefaultValue(i));
    }
    this.P = P;
    var parts = { ids: [], index: Object.create(null) };
    for (i = 0; i < cm.getPartCount(); i++) { var pid = cm.getPartId(i).getString().s; parts.ids.push(pid); parts.index[pid] = i; }
    this.Parts = parts;
  };

  /* the model's mappings, ready to run: neutral input per mapping from the output's default */
  Avatar.prototype._compile = function () {
    var P = this.P, rows = (this.mset && this.mset.mappings) || [], out = [], names = [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i], idx = P.index[r.output];
      if (r.input) names.push(String(r.input));            // a row that cannot run still names its input
      if (idx === undefined) continue;
      var in0 = +r.in[0], in1 = +r.in[1], o0 = +r.out[0], o1 = +r.out[1];
      if (in0 === in1 || o0 === o1) continue;
      var def = P.def[idx], t = (def - o0) / (o1 - o0), neutral = in0 + clamp(t, 0, 1) * (in1 - in0);
      var kind = r.breath && !r.input ? 'breath' : (FACTOR[r.input] ? 'factor' : (CENTERED[r.input] ? 'centered' : 'abs'));
      // a head-turn input driving something other than the head (ParamBodyAngleX, a step ...) gets
      // the tracker's body signal, which is slower and calmer than the head's
      var body = /^FaceAngle/.test(r.input) && !/^param_?angle_?[xyz]$/i.test(r.output);
      out.push({ idx: idx, input: r.input, in0: in0, in1: in1, o0: o0, o1: o1, cin: r.clamp_in !== false, cout: r.clamp_out !== false,
                 tau: (r.smoothing || 0) / 100 * 0.28, blink: !!r.blink, breath: !!r.breath, kind: kind, body: body,
                 neutral: neutral, s: null, lo: Math.min(o0, o1), hi: Math.max(o0, o1) });
    }
    this.maps = out;
    this._setInputs(names);
  };

  Avatar.prototype._reportInfo = async function () {
    if (this.stage.role !== 'obs' && this.stage.role !== 'preview') return;
    var im = this.model.internalModel, cm = im.coreModel, P = this.P, names = {}, groups = {}, partNames = {};
    try {
      var di = (this.spec.FileReferences || {}).DisplayInfo;
      if (di) {
        var cdi = await getJSON(resolveUrl(di, this.spec.url));
        (cdi.ParameterGroups || []).forEach(function (g) { groups[g.Id] = g.Name; });
        (cdi.Parameters || []).forEach(function (p) { names[p.Id] = { name: p.Name, group: groups[p.GroupId] || '' }; });
        (cdi.Parts || []).forEach(function (p) { partNames[p.Id] = p.Name; });
      }
    } catch (e) {}
    var params = P.ids.map(function (id, i) {
      return { id: id, name: (names[id] || {}).name || '', group: (names[id] || {}).group || '',
               min: +P.min[i].toFixed(4), max: +P.max[i].toFixed(4), default: +P.def[i].toFixed(4) };
    });
    var parts = [], drawables = [];
    for (var i = 0; i < cm.getPartCount(); i++) { var pid = cm.getPartId(i).getString().s; parts.push({ id: pid, name: partNames[pid] || '' }); }
    for (i = 0; i < cm.getDrawableCount(); i++) drawables.push(cm.getDrawableId(i).getString().s);
    var hit = Object.keys(im.hitAreas || {});
    this.info = { parameters: params, parts: parts, drawables: drawables, hit_areas: hit,
                  size: [im.originalWidth, im.originalHeight] };
    this.stage.emit('model_info', { model: this.modelId, info: this.info });
  };

  /* -------------------------------------------- the frame: what the tracker wants this frame */

  Avatar.prototype.update = function (dt, now) {
    var t0 = performance.now();
    this._tickTransform(now);
    this.alpha.cur = approach(this.alpha.cur, this.alpha.to, dt, this.alpha.rate / 3);
    if (Math.abs(this.alpha.cur - this.alpha.to) < 0.002) this.alpha.cur = this.alpha.to;
    this.node.alpha = this.alpha.cur;
    this.node.visible = this.alpha.cur > 0.002;
    if (!this.node.visible || !this.model) { this._lightTick(dt, now); return; }
    var fps = (this.cfg.idle && this.cfg.idle.fps) || 60;
    this.acc += dt;
    if (fps < 59 && this.acc < 1 / fps - 0.002) return;
    var step = this.acc;
    this.acc = 0;
    this._drive(step, now);
    if (this.png) { var tp = performance.now(); this.png.update(step, this.frame, now); this.msAcc += performance.now() - tp; }
    else this.model.update(step * 1000);
    for (var i = 0; i < this.items.length; i++) this.items[i].tick(step);
    this._lightTick(step, now);
    this.msAcc += performance.now() - t0;
    this.msN++;
  };

  Avatar.prototype._drive = function (dt, now) {
    var c = this.cfg, idle = c.idle || {}, t = now;
    var sp = this.speech.cur, m = c.mouth || {};
    // lipsync: speech we play, or the recorded device
    var lip = null;
    if (m.lipsync !== false && (sp || (m.source === 'device' && m.device))) lip = this.lip.update(dt);
    else { this.lip.reset(); lip = this.lip.out; }
    // weights of overrides / expressions fade toward their targets
    var k, o;
    for (k in this.ov) {
      o = this.ov[k];
      if (o.until && now >= o.until) { o.to = 0; o.until = 0; o.rate = o.fade; }
      if (o.to > o.w) o.w = Math.min(o.to, o.w + (o.rateIn > 0 ? dt / o.rateIn : 1));
      else if (o.to < o.w) o.w = Math.max(o.to, o.w - (o.rate > 0 ? dt / o.rate : 1));
      if (o.w <= 0 && o.to <= 0) delete this.ov[k];
    }
    for (k in this.partOv) {
      o = this.partOv[k];
      if (o.until && now >= o.until) { o.to = 0; o.until = 0; }
      if (o.to > o.w) o.w = Math.min(o.to, o.w + dt / Math.max(0.001, o.fade));
      else if (o.to < o.w) o.w = Math.max(o.to, o.w - dt / Math.max(0.001, o.fade));
      if (o.w <= 0 && o.to <= 0) delete this.partOv[k];
    }
    for (k in this.exprs) {
      var e = this.exprs[k];
      if (e.until && now >= e.until) { e.to = 0; e.until = 0; }
      if (e.to > e.w) e.w = Math.min(e.to, e.w + dt / Math.max(0.01, e.fadeIn));
      else if (e.to < e.w) e.w = Math.max(e.to, e.w - dt / Math.max(0.01, e.fadeOut));
      if (e.w <= 0 && e.to <= 0) delete this.exprs[k];
    }
    // face: emotion + API face controls, blended toward
    if (this.emo.until && now >= this.emo.until) this._emotionOff();
    if (this.apiFace.until && now >= this.apiFace.until) { this.apiFace = { face: {}, until: 0, tau: 0.3 }; }
    var ft = { smile: 0, brows: 0, eyes: 1, mouth_open: 0, cheek: 0, mouth_x: 0, tilt: 0, wink_left: 0, wink_right: 0 };
    var ef = this.emo.face || {}, ei = this.emo.intensity == null ? 1 : this.emo.intensity;
    for (k in ef) ft[k] = k === 'eyes' ? lerp(1, ef[k], ei) : ef[k] * ei;
    var af = this.apiFace.face || {};
    for (k in af) ft[k] = af[k];
    var fn = this.faceNow, tau = Math.min(this.emo.tau, this.apiFace.tau);
    for (k in ft) fn[k] = approach(fn[k], ft[k], dt, tau);
    // gaze: a look target, else the eyes wander (deterministic slots: OBS and the preview agree)
    var gz = this.gaze, gs = (idle.gaze == null ? 50 : idle.gaze) / 50, tx = 0, ty = 0, head = 0.22 * gs;
    if (this.look && this.look.until && now >= this.look.until) this.look = null;
    if (this.look) {
      var lk = this.look;
      if (lk.stage_x != null) {
        var hp = this._headPoint();
        tx = clamp((lk.stage_x / 100 * W - hp.x) / 700, -1, 1);
        ty = clamp(-(lk.stage_y / 100 * H - hp.y) / 450, -1, 1);
      } else { tx = lk.x; ty = lk.y; }
      head = lk.head == null ? 1 : lk.head;
      gz.speed = lk.speed || 1;
    } else if (gs > 0) {
      var slot = Math.floor(t / 1.7), r1 = hash(this.seed, slot), r2 = hash(this.seed + 7, slot), r3 = hash(this.seed + 13, slot);
      var off = r3 * 1.7;                                  // the saccade happens somewhere in the slot
      var use = (t - slot * 1.7) >= off ? slot : slot - 1;
      r1 = hash(this.seed, use); r2 = hash(this.seed + 7, use);
      var away = hash(this.seed + 21, use) < 0.3;          // mostly near the camera, sometimes away
      tx = (r1 * 2 - 1) * (away ? 0.75 : 0.22) * gs;
      ty = (r2 * 2 - 1) * (away ? 0.45 : 0.14) * gs;
      gz.speed = 1;
    }
    for (var gi = 0; gi < this.gestures.length; gi++) {
      var g0 = this.gestures[gi];
      if (g0.name === 'look_away' && now < g0.t0 + g0.dur) { tx = (hash(this.seed, Math.floor(g0.t0)) > 0.5 ? 0.8 : -0.8) * g0.amount; ty = -0.25; }
    }
    gz.ex = approach(gz.ex, tx, dt, 0.035 / (gz.speed || 1));
    gz.ey = approach(gz.ey, ty, dt, 0.035 / (gz.speed || 1));
    var follow = this.look ? 0.32 : 0.6;                   // a wandering glance turns the head only a little, slowly
    gz.hx = approach(gz.hx, tx * head, dt, follow / (gz.speed || 1));
    gz.hy = approach(gz.hy, ty * head, dt, follow / (gz.speed || 1));
    // idle sway: the head drifts a little, the body has a slower sway of its own. (A model's FaceAngleX
    // usually drives its body too; fed the head's movement the body would swing with every glance.)
    var sw = (idle.sway == null ? 50 : idle.sway) / 50;
    var hx = fbm(this.seed + 1, t * 0.19) * 3.5 * sw, hy = fbm(this.seed + 2, t * 0.16) * 2.5 * sw, hz = fbm(this.seed + 3, t * 0.14) * 2.2 * sw;
    var bx = fbm(this.seed + 8, t * 0.07) * 3 * sw, by = fbm(this.seed + 9, t * 0.06) * 2 * sw, bz = fbm(this.seed + 10, t * 0.05) * 2.4 * sw;
    var px = fbm(this.seed + 4, t * 0.08) * 0.3 * sw;
    var ax = 0, ay = 0, az = 0, lx = 0, lz = 0;          // head movement on top of the sway / leans (the body leans too)
    // head motion while talking: nods with the emphasis of the voice
    var smv = (idle.speech_motion == null ? 50 : idle.speech_motion) / 50, lvl = lip ? lip.level : 0;
    this.env.fast = approach(this.env.fast, lvl, dt, 0.06);
    this.env.slow = approach(this.env.slow, lvl, dt, 0.45);
    var emph = clamp(this.env.fast - this.env.slow, -0.5, 1);
    ay += -emph * 9 * smv + this.env.slow * 2.5 * smv;
    ax += fbm(this.seed + 5, t * 0.9) * 4 * this.env.slow * smv;
    az += fbm(this.seed + 6, t * 0.7) * 2.5 * this.env.slow * smv;
    var browLift = clamp(emph, 0, 1) * 0.35 * smv;
    // gestures
    for (gi = this.gestures.length - 1; gi >= 0; gi--) {
      var g = this.gestures[gi], u = (now - g.t0) / g.dur, a = g.amount;
      if (u >= 1) { this.gestures.splice(gi, 1); continue; }
      var env = Math.sin(Math.PI * clamp(u, 0, 1));
      switch (g.name) {
        case 'nod': ay += -Math.sin(Math.PI * 2 * u) * 12 * a * env; break;
        case 'double_nod': ay += -Math.sin(Math.PI * 4 * u) * 10 * a * env; break;
        case 'shake': ax += Math.sin(Math.PI * 6 * u) * 16 * a * env; break;
        case 'tilt': az += 14 * a * env; break;
        case 'bounce': ay += Math.abs(Math.sin(Math.PI * 3 * u)) * 10 * a * env; px += 0; break;
        case 'lean_left': lz += 10 * a * env; lx += -9 * a * env; px -= 0.6 * a * env; break;
        case 'lean_right': lz += -10 * a * env; lx += 9 * a * env; px += 0.6 * a * env; break;
      }
    }
    az += -fn.tilt * 12;
    ax += gz.hx * 24;
    ay += gz.hy * 14;
    // blink: slots of 0.5 s, each may start one (never two within 1.5 s); sometimes a double blink
    var bl = 1;
    if (idle.blink !== false) {
      var bs = Math.floor(t / 0.5);
      for (var j = 0; j < 3; j++) {
        var s0 = bs - j;
        if (hash(this.seed + 31, s0) < 0.13 && !(hash(this.seed + 31, s0 - 1) < 0.13) && !(hash(this.seed + 31, s0 - 2) < 0.13)) {
          var start = s0 * 0.5 + hash(this.seed + 37, s0) * 0.5, d = t - start;
          var dbl = hash(this.seed + 41, s0) < 0.14;
          bl = Math.min(bl, blinkCurve(d), dbl ? blinkCurve(d - 0.26) : 1);
        }
      }
    }
    var breath = idle.breath !== false ? (Math.sin(t * Math.PI * 2 / 3.6 + (this.seed % 100)) + 1) / 2 : 0;
    var talking = lip && lip.level > 0.02;
    var mopen = Math.max(lip ? lip.open : 0, fn.mouth_open);
    this.frame = {
      dt: dt,
      FaceAngleX: hx + ax + lx, FaceAngleY: hy + ay, FaceAngleZ: hz + az + lz, FacePositionX: px,
      BodyAngleX: bx + ax * 0.2 + lx, BodyAngleY: by + ay * 0.2, BodyAngleZ: bz + az * 0.2 + lz,
      EyeX: gz.ex, EyeY: gz.ey,
      eyeL: fn.eyes * bl * (1 - fn.wink_left), eyeR: fn.eyes * bl * (1 - fn.wink_right),
      Brows: fn.brows * 0.5 + browLift * 0.5,
      MouthSmile: fn.smile * 0.5 + (talking ? lip.form * 0.32 : 0), MouthX: fn.mouth_x,
      MouthOpen: mopen, CheekPuff: fn.cheek, lip: lip, breath: breath, blink: bl
    };
  };

  function blinkCurve(d) {                         // 1 open .. 0 shut .. 1 open, ~0.17 s
    if (d < 0 || d > 0.2) return 1;
    if (d < 0.07) return 1 - d / 0.07;
    if (d < 0.09) return 0;
    return clamp((d - 0.09) / 0.11, 0, 1);
  }

  /* the tracker's value of an input for one mapping */
  Avatar.prototype._inputValue = function (mp, F) {
    var lip = F.lip || {}, inp = mp.input;
    switch (mp.kind) {
      case 'breath': return mp.in0 + F.breath * (mp.in1 - mp.in0);
      case 'factor': return mp.neutral * (inp === 'EyeOpenLeft' ? F.eyeL : F.eyeR);
      case 'centered':
        var d = 0;
        switch (inp) {
          case 'FaceAngleX': d = mp.body ? F.BodyAngleX : F.FaceAngleX; break;
          case 'FaceAngleY': d = mp.body ? F.BodyAngleY : F.FaceAngleY; break;
          case 'FaceAngleZ': d = mp.body ? F.BodyAngleZ : F.FaceAngleZ; break;
          case 'FacePositionX': d = F.FacePositionX; break;
          case 'EyeRightX': case 'EyeLeftX': d = -F.EyeX; break;     // VTS' eye X runs mirrored
          case 'EyeRightY': case 'EyeLeftY': d = F.EyeY; break;
          case 'Brows': case 'BrowLeftY': case 'BrowRightY': d = F.Brows; break;
          case 'MouthSmile': d = F.MouthSmile; break;
          case 'MouthX': d = F.MouthX * 0.5; break;
        }
        return mp.neutral + d;
      default:
        switch (inp) {
          case 'MouthOpen': case 'VoiceVolumePlusMouthOpen': return F.MouthOpen;
          case 'CheekPuff': return F.CheekPuff;
          case 'VoiceA': return lip.A || 0;
          case 'VoiceI': return lip.I || 0;
          case 'VoiceU': return lip.U || 0;
          case 'VoiceE': return lip.E || 0;
          case 'VoiceO': return lip.O || 0;
          case 'VoiceSilence': return lip.silence == null ? 1 : lip.silence;
          case 'VoiceVolume': return lip.volume || 0;
          case 'VoiceFrequency': case 'VoiceFrequencyPlusMouthSmile': return lip.level > 0.02 ? lip.frequency : 0.5;
        }
        return mp.neutral;
    }
  };

  /* input layer: after the motions, before expressions are added and physics runs */
  Avatar.prototype._inputLayer = function (cm) {
    var F = this.frame;
    if (!F || !this.maps) return;
    var dt = F.dt, maps = this.maps, ov = this.ov, own = this.own, i, o, k;
    this._trackMotion();
    var idleAnim = this.motionPri <= 1, talking = !!(F.lip && F.lip.level > 0.01) || !!this.speech.cur;
    for (k in own) {                                       // parameters a motion is animating
      var ow = own[k];
      ow.w = ow.to > ow.w ? Math.min(1, ow.w + dt / 0.3) : Math.max(0, ow.w - dt / 0.45);
      if (ow.w <= 0 && ow.to <= 0) delete own[k];
    }
    for (i = 0; i < maps.length; i++) {
      var mp = maps[i], v = this._inputValue(mp, F);
      o = ov[mp.input];
      if (o && o.isInput) v = o.mode === 'add' ? v + o.value * o.weight * o.w : lerp(v, o.value, o.weight * o.w);
      var tt = (v - mp.in0) / (mp.in1 - mp.in0);
      if (mp.cin) tt = clamp(tt, 0, 1);
      var out = mp.o0 + tt * (mp.o1 - mp.o0);
      if (mp.cout) out = clamp(out, mp.lo, mp.hi);
      mp.s = mp.s == null ? out : approach(mp.s, out, dt, mp.tau);
      var val = mp.s, own1 = own[mp.idx];
      if (own1 && own1.w > 0 && !(MOUTH_KEEP[mp.input] && (idleAnim || talking)) && !(idleAnim && FACE_KEEP[mp.input]))
        val = lerp(val, cm.getParameterValueByIndex(mp.idx), own1.w);
      // "use blinking" on a row whose input is not EyeOpenLeft/Right (a custom eye input the API holds):
      // the blink closes its output (toward out[0]) on top of that value. The standard eye rows get
      // their blink through the input itself (kind 'factor').
      if (mp.blink && F.blink < 1 && mp.kind !== 'factor' && mp.kind !== 'breath') val = lerp(mp.o0, val, F.blink);
      cm.setParameterValueByIndex(mp.idx, val);
    }
    // expressions, stacked (Cubism's own Add / Multiply / Overwrite)
    for (k in this.exprs) {
      var e = this.exprs[k];
      if (!e.data || e.w <= 0) continue;
      for (i = 0; i < e.data.length; i++) {
        var p = e.data[i], idx = p.idx, cur = cm.getParameterValueByIndex(idx), w = e.w;
        if (p.blend === 'Multiply') cur = cur * (1 + (p.value - 1) * w);
        else if (p.blend === 'Overwrite') cur = cur + (p.value - cur) * w;
        else cur = cur + p.value * w;
        cm.setParameterValueByIndex(idx, cur);
      }
    }
    this._applyOverrides(cm, 'input');
    this._applyParts(cm);
  };

  Avatar.prototype._finalLayer = function (cm) { this._applyOverrides(cm, 'final'); };

  Avatar.prototype._applyOverrides = function (cm, layer) {
    var P = this.P;
    for (var k in this.ov) {
      var o = this.ov[k];
      if (o.isInput || o.layer !== layer || o.w <= 0) continue;
      var idx = P.index[k];
      if (idx === undefined) continue;
      var cur = cm.getParameterValueByIndex(idx);
      var target = o.mode === 'add' ? cur + o.value : o.value;
      if (o.dur > 0 && o.from != null) {                 // eased toward its value over `duration`
        var u = ease(o.ease, (performance.now() - o.started) / (o.dur * 1000));
        target = o.mode === 'add' ? cur + o.value * u : lerp(o.from, o.value, u);
      }
      cm.setParameterValueByIndex(idx, lerp(cur, clamp(target, P.min[idx], P.max[idx]), o.weight * o.w));
    }
  };

  /* which parameters the playing motion animates (they are left to it) */
  Avatar.prototype._trackMotion = function () {
    var mm = this.model.internalModel.motionManager, st = mm.state || {};
    var key = st.currentGroup != null && st.currentPriority > 0 ? st.currentGroup + '#' + st.currentIndex : '';
    this.motionPri = st.currentPriority || 0;
    if (key === this.motionKey) return;
    this.motionKey = key;
    var own = this.own, self = this;
    for (var k in own) own[k].to = 0;
    this.partCurves = [];
    if (!key) return;
    var def = (((this.spec.FileReferences || {}).Motions || {})[st.currentGroup] || [])[st.currentIndex];
    if (!def) return;
    getJSON(resolveUrl(def.File, this.spec.url)).then(function (j) {
      if (self.motionKey !== key) return;
      var im = self.model.internalModel, cm = im.coreModel, pc = [];
      (j.Curves || []).forEach(function (c) {
        if (c.Target === 'PartOpacity') {
          // Cubism writes a part curve to a stand-in parameter of the part's id, which only a
          // pose file reads; models without one (VTube Studio's Hiyori ...) rely on the app
          // applying it to the part, so it is applied here
          var pi = self.Parts.index[c.Id];
          if (pi !== undefined) pc.push({ pi: pi, vi: cm.getParameterIndex(im.idManager.getId(c.Id)) });
          return;
        }
        if (c.Target !== 'Parameter') return;
        var idx = self.P.index[c.Id];
        if (idx !== undefined) own[idx] = { w: (own[idx] || {}).w || 0, to: 1 };
      });
      self.partCurves = pc;
    }).catch(function () {});
  };

  /* Parts at rest, for a model without a pose file whose motions switch parts (two arm sets ...):
     the opening value of each part curve in its idle motion, else in the first motion that has
     one - so the model never shows every variant at once, idle animation or not. */
  Avatar.prototype._restParts = async function (idleGroup) {
    var im = this.model.internalModel, motions = (this.meta && this.meta.motions) || [], self = this, seq = this.loadSeq;
    if (im.pose || !motions.length) return;
    var first = motions.filter(function (m) { return m.group === idleGroup; }).concat(motions.filter(function (m) { return m.group !== idleGroup; }));
    var rest = {};
    for (var i = 0; i < first.length && i < 24; i++) {
      var j;
      try { j = await getJSON(resolveUrl(first[i].file, this.spec.url)); } catch (e) { continue; }
      if (seq !== this.loadSeq) return;
      (j.Curves || []).forEach(function (c) {
        var pi = c.Target === 'PartOpacity' ? self.Parts.index[c.Id] : undefined;
        if (pi !== undefined && !(pi in rest) && c.Segments && c.Segments.length > 1) rest[pi] = clamp(+c.Segments[1], 0, 1);
      });
    }
    this.restPartOpacity = rest;
  };

  /* part opacities: rest values, the playing motion's part curves, then the API's `parts` */
  Avatar.prototype._applyParts = function (cm) {
    var rest = this.restPartOpacity, pc = this.partCurves || [], k, i;
    if (rest) {
      for (i = 0; i < pc.length; i++) rest[pc[i].pi] = clamp(cm.getParameterValueByIndex(pc[i].vi), 0, 1);   // stays after the motion
      for (k in rest) cm.setPartOpacityByIndex(+k, rest[k]);
    }
    for (k in this.partOv) {
      var o = this.partOv[k], pi = this.Parts.index[k];
      if (pi === undefined || o.w <= 0) continue;
      cm.setPartOpacityByIndex(pi, lerp(cm.getPartOpacityByIndex(pi), o.value, o.w));
    }
  };

  Avatar.prototype._headPoint = function () {
    var b = this.model ? this.model.getBounds() : null;
    if (!b) return { x: this.tr.cur.x / 100 * W, y: this.tr.cur.y / 100 * H };
    var s = this.stage.scale || 1, r = this.stage.root.position;
    return { x: (b.x + b.width / 2 - r.x) / s, y: (b.y + b.height * 0.22 - r.y) / s };
  };

  /* -------------------------------------------- placement */

  Avatar.prototype._tweenTo = function (to, dur, easeName) {
    var cur = this.tr.cur;
    var target = { x: to.x != null ? to.x : cur.x, y: to.y != null ? to.y : cur.y, scale: to.scale != null ? to.scale : cur.scale,
                   rotation: to.rotation != null ? to.rotation : cur.rotation };
    if (this.tr.to && target.x === this.tr.to.x && target.y === this.tr.to.y && target.scale === this.tr.to.scale && target.rotation === this.tr.to.rotation) return;
    this.tr.from = { x: cur.x, y: cur.y, scale: cur.scale, rotation: cur.rotation };
    this.tr.to = target;
    this.tr.t0 = nowSec();
    this.tr.dur = dur || 0;
    this.tr.ease = easeName || 'smooth';
    if (!dur) { this.tr.cur = { x: target.x, y: target.y, scale: target.scale, rotation: target.rotation }; this._placeNode(); }
  };

  Avatar.prototype.setTransformNow = function (t) {
    var cur = this.tr.cur;
    for (var k in t) cur[k] = t[k];
    this.tr.to = { x: cur.x, y: cur.y, scale: cur.scale, rotation: cur.rotation };
    this.tr.dur = 0;
    this._placeNode();
  };

  Avatar.prototype._tickTransform = function (now) {
    var tr = this.tr;
    if (tr.dur > 0 && tr.to) {
      var u = ease(tr.ease, (now - tr.t0) / tr.dur);
      tr.cur = { x: lerp(tr.from.x, tr.to.x, u), y: lerp(tr.from.y, tr.to.y, u), scale: lerp(tr.from.scale, tr.to.scale, u),
                 rotation: lerp(tr.from.rotation, tr.to.rotation, u) };
      if (u >= 1) tr.dur = 0;
    }
    this._placeNode();
  };

  Avatar.prototype._placeNode = function () {
    var c = this.tr.cur;
    this.node.position.set(c.x / 100 * W, c.y / 100 * H);
    this.node.scale.set(c.scale * (this.tr.flip ? -1 : 1), c.scale);
    this.node.rotation = c.rotation * Math.PI / 180;
  };

  Avatar.prototype._fadeTo = function (to, secs) { this.alpha.to = to; this.alpha.rate = Math.max(0.01, secs || 0.01); };

  /* is a page point (renderer pixels) on the model? (its triangles, not just its box) */
  Avatar.prototype.hit = function (gx, gy) {
    var m = this.model;
    if (!m) return false;
    if (this.png) return this.png.hit(gx, gy);
    var b = m.getBounds();
    if (gx < b.x || gy < b.y || gx > b.x + b.width || gy > b.y + b.height) return false;
    var lp = m.toLocal(new PIXI.Point(gx, gy));
    return this.meshAt(lp.x, lp.y) !== null;
  };

  /* the topmost visible art mesh under a model-space point: {index, id, tri, bary} or null */
  Avatar.prototype.meshAt = function (x, y) {
    if (this.png) return null;
    var im = this.model.internalModel, cm = im.coreModel, n = cm.getDrawableCount();
    var orders = cm.getDrawableRenderOrders(), list = [];
    for (var i = 0; i < n; i++) list.push(i);
    list.sort(function (a, b) { return orders[b] - orders[a]; });
    for (var li = 0; li < list.length; li++) {
      var d = list[li];
      if (!cm.getDrawableDynamicFlagIsVisible(d) || cm.getDrawableOpacity(d) < 0.15) continue;
      var v = im.getDrawableVertices(d), ind = cm.getDrawableVertexIndices(d);
      for (var t = 0; t + 2 < ind.length; t += 3) {
        var a = ind[t], bb = ind[t + 1], c = ind[t + 2];
        var bc = bary(x, y, v[a * 2], v[a * 2 + 1], v[bb * 2], v[bb * 2 + 1], v[c * 2], v[c * 2 + 1]);
        if (bc) return { index: d, id: cm.getDrawableId(d).getString().s, tri: [a, bb, c], bary: bc,
                         angle: Math.atan2(v[bb * 2 + 1] - v[a * 2 + 1], v[bb * 2] - v[a * 2]) };
      }
    }
    return null;
  };

  /* where an item dragged to a page point sits: % of the model's box (see Item._place) */
  Avatar.prototype.itemPos = function (it, gx, gy, dx, dy) {
    var lp = this.node.toLocal(new PIXI.Point(gx, gy));
    var bw = this.boxWidth();
    return { x: Math.round(((lp.x + (dx || 0)) / bw * 100 + 50) * 10) / 10, y: Math.round(((lp.y + (dy || 0)) / H * 100 + 50) * 10) / 10 };
  };

  /* a pin for an item at its current spot: the art mesh triangle under its centre, or null */
  Avatar.prototype.pinAt = function (it) {
    if (!this.model || !it.node) return null;
    if (this.png) {                                        // a PNGtuber: pinned to the layer under the item
      var g0 = it.node.getGlobalPosition(), rp = this.png.draw.toLocal(g0), hitL = this.png.layerAt(rp.x, rp.y);
      return hitL ? { mesh: 'layer:' + hitL.id, tri: [0, 0, 0], bary: [hitL.lx, hitL.ly, 0], angle0: 0, follow_angle: true } : null;
    }
    var g = it.node.getGlobalPosition(), lp = this.model.toLocal(g), hit = this.meshAt(lp.x, lp.y);
    return hit ? { mesh: hit.id, tri: hit.tri, bary: hit.bary, angle0: hit.angle, follow_angle: true } : null;
  };

  function bary(px, py, ax, ay, bx, by, cx, cy) {
    var v0x = bx - ax, v0y = by - ay, v1x = cx - ax, v1y = cy - ay, v2x = px - ax, v2y = py - ay;
    var den = v0x * v1y - v1x * v0y;
    if (Math.abs(den) < 1e-9) return null;
    var v = (v2x * v1y - v1x * v2y) / den, w = (v0x * v2y - v2x * v0y) / den, u = 1 - v - w;
    return u >= -1e-4 && v >= -1e-4 && w >= -1e-4 ? [u, v, w] : null;
  }

  /* -------------------------------------------- commands */

  Avatar.prototype.restore = function (live) {
    var k, self = this;
    for (k in live.params || {}) this.command(Object.assign({}, live.params[k], { cmd: 'params', restore: true }));
    for (k in live.parts || {}) this.command(Object.assign({}, live.parts[k], { cmd: 'parts', restore: true }));
    for (k in live.expressions || {}) this.command({ cmd: 'expression', name: k, state: 'on', until: live.expressions[k] || null, fade: 0 });
    if (live.emotion) this.command(Object.assign({ cmd: 'emotion', fade: 0, previous: [] }, live.emotion));
    if (live.face) this.command(Object.assign({ cmd: 'face', fade: 0 }, live.face));
    if (live.look) this.command(Object.assign({ cmd: 'look' }, live.look));
    if (live.light) { this.lightOverride = true; this.setLight(live.light, 0); }
    if (live.transform) {
      this.tr.override = live.transform;
      setTimeout(function () { self._tweenTo(live.transform, 0); if (live.transform.flip != null) self.tr.flip = live.transform.flip; }, 0);
    }
    if (live.visible != null) { this.alpha.override = live.visible; this._fadeTo(live.visible ? 1 : 0, 0.01); }
  };

  Avatar.prototype.command = function (c) {
    var now = nowSec(), k;
    switch (c.cmd) {
      case 'params': {
        var vals = c.values || {};
        for (k in vals) {
          var prev = this.ov[k];
          this.ov[k] = { value: vals[k], mode: c.mode || 'set', layer: c.layer || 'input', weight: c.weight == null ? 1 : c.weight,
                         isInput: null, duration: c.duration || 0, restore: !!c.restore,       // isInput, rateIn, dur and from: _classify
                         w: c.restore ? 1 : (prev ? prev.w : 0), to: 1, rateIn: 0.0001,
                         rate: 0.3, fade: c.fade == null ? 0.3 : c.fade, until: c.until || 0, dur: 0,
                         from: null, started: performance.now(), ease: c.ease || 'smooth' };
          this._classify(k, this.ov[k]);
          if (c.hold === false && !c.until) this.ov[k].until = now + Math.max(0.05, c.duration || 0) + 0.05;
        }
        break;
      }
      case 'parts': {                                       // show / hide parts: {"PartArmA": 0, "PartArmB": 1}
        var pv = c.values || {};
        for (k in pv) {
          var prevP = this.partOv[k];
          this.partOv[k] = { value: clamp(+pv[k], 0, 1), w: c.restore || !c.fade ? 1 : (prevP ? prevP.w : 0), to: 1,
                             fade: c.fade == null ? 0.3 : c.fade, until: c.until || 0 };
        }
        break;
      }
      case 'release_parts': {
        var pids = c.ids || Object.keys(this.partOv);
        for (var pi2 = 0; pi2 < pids.length; pi2++) { var po = this.partOv[pids[pi2]]; if (po) { po.to = 0; po.until = 0; po.fade = c.fade == null ? 0.3 : c.fade; } }
        break;
      }
      case 'release': {
        var ids = c.ids || Object.keys(this.ov);
        for (var i = 0; i < ids.length; i++) { var o = this.ov[ids[i]]; if (o) { o.to = 0; o.rate = c.fade == null ? 0.3 : c.fade; o.until = 0; } }
        break;
      }
      case 'expression':                                   // one, or several in the same frame
        if (this.png) {                                    // a PNGtuber shows one state at a time
          var sts = c.states || (function () { var o = {}; o[c.name] = c.state === 'off' ? 'off' : 'on'; return o; })();
          for (k in sts) {
            if (sts[k] === 'on') this.png.setState(k);
            else if (this.png.state === k) this.png.setState(null);
          }
          break;
        }
        if (c.states) { for (k in c.states) this._expression(k, c.states[k] === 'on', c.fade, c.until); }
        else this._expression(c.name, c.state !== 'off', c.fade, c.until);
        break;
      case 'clear_expressions': if (this.png) this.png.setState(null); for (k in this.exprs) { this.exprs[k].to = 0; this.exprs[k].fadeOut = c.fade || this.exprs[k].fadeOut; } break;
      case 'motion': this._motion(c); break;
      case 'stop_motion': if (this.model && !this.png) this.model.internalModel.motionManager.stopAllMotions(); break;
      case 'emotion': {
        var self = this;
        if (this.png) this.png.setState(c.name === 'neutral' ? null : ((c.expressions || [])[0] || c.name));
        if (!this.png) {
          (c.previous || []).forEach(function (n) { if ((c.expressions || []).indexOf(n) < 0) self._expression(n, false, c.fade); });
          (c.expressions || []).forEach(function (n) { self._expression(n, true, c.fade, c.until); });
        }
        this.emo = { name: c.name, face: c.name === 'neutral' ? {} : (c.face || {}), intensity: c.intensity == null ? 1 : c.intensity,
                     until: c.until || 0, tau: Math.max(0.02, (c.fade == null ? 0.5 : c.fade) / 3), exprs: c.expressions || [] };
        if (c.motion) this._motion({ name: c.motion });
        break;
      }
      case 'face':
        this.apiFace = c.release ? { face: {}, until: 0, tau: Math.max(0.02, (c.fade || 0.3) / 3) }
          : { face: c.face || {}, until: c.until || 0, tau: Math.max(0.02, (c.fade == null ? 0.3 : c.fade) / 3) };
        break;
      case 'look': this.look = c.release ? null : { x: c.x || 0, y: c.y || 0, stage_x: c.stage_x, stage_y: c.stage_y, head: c.head, until: c.until || 0, speed: c.speed || 1 }; break;
      case 'gesture':
        if (this.png && c.name === 'bounce') this.png.hop(c.amount == null ? 1 : c.amount);
        this.gestures.push({ name: c.name, amount: c.amount == null ? 1 : c.amount, t0: now,
                                           dur: c.duration || { nod: 0.7, double_nod: 0.9, shake: 0.9, tilt: 1.4, bounce: 0.8, lean_left: 1.8, lean_right: 1.8, look_away: 1.6 }[c.name] || 1 }); break;
      case 'transform': {
        var t = c.transform || {};
        if (c.save) this.tr.override = null;
        else this.tr.override = Object.assign({}, this.tr.override || {}, t);
        if (t.flip != null) this.tr.flip = t.flip;
        this._tweenTo(t, c.duration || 0, c.ease);
        break;
      }
      case 'visible':
        this.alpha.override = c.save ? null : c.visible;
        this._fadeTo(c.visible ? 1 : 0, c.fade == null ? 0.4 : c.fade);
        break;
      case 'speak': this.speak(c); break;
      case 'stop_speaking': this.stopSpeaking(); break;
      case 'light': this.lightOverride = !c.save && !c.reset; this.setLight(c.light, c.fade == null ? 0.6 : c.fade); break;
      case 'item_add': case 'item_remove': case 'item_update': case 'items_clear': break;   // arrives as config
      case 'reload': if (this.cfg.model) this.load(this.cfg.model); break;
    }
  };

  Avatar.prototype._emotionOff = function () {
    var self = this;
    if (this.png) this.png.setState(null);
    (this.emo.exprs || []).forEach(function (n) { self._expression(n, false, 0.5); });
    this.emo = { name: 'neutral', face: {}, intensity: 1, until: 0, tau: 0.25, exprs: [] };
  };

  Avatar.prototype._expression = function (name, on, fade, until) {
    var e = this.exprs[name];
    if (!on) { if (e) { e.to = 0; if (fade != null) e.fadeOut = Math.max(0.01, fade); } return; }
    if (!e) e = this.exprs[name] = { w: fade === 0 ? 1 : 0, to: 1, fadeIn: 0.4, fadeOut: 0.4, data: null, until: until || 0 };
    e.to = 1;
    e.until = until || 0;
    if (fade != null) { e.fadeIn = Math.max(0.01, fade); if (fade === 0) e.w = 1; }
    this._loadExpr(name);
  };

  Avatar.prototype._loadExpr = function (name) {
    var e = this.exprs[name], self = this;
    if (!e || e.data || e.loading || !this.model || !this.meta) return;
    var def = (this.meta.expressions || []).filter(function (x) { return x.name === name; })[0];
    if (!def) { this.stage.emit('event', { event: 'error', avatar: this.name, message: 'no expression "' + name + '"' }); delete this.exprs[name]; return; }
    e.loading = true;
    getJSON(resolveUrl(def.file, this.spec.url)).then(function (j) {
      e.loading = false;
      e.data = (j.Parameters || []).map(function (p) { return { idx: self.P.index[p.Id], value: +p.Value || 0, blend: p.Blend || 'Add' }; })
        .filter(function (p) { return p.idx !== undefined; });
      if (j.FadeInTime != null && e.fadeIn === 0.4) e.fadeIn = Math.max(0.01, +j.FadeInTime);
      if (j.FadeOutTime != null && e.fadeOut === 0.4) e.fadeOut = Math.max(0.01, +j.FadeOutTime);
    }).catch(function () { e.loading = false; });
  };

  Avatar.prototype._motion = function (c) {
    if (!this.model) return;
    if (this.png) { this.stage.emit('event', { event: 'error', avatar: this.name, message: 'a PNGtuber has no motions (use its states)' }); return; }
    var meta = this.meta || {}, group = c.group, index = c.index, self = this;
    if (c.name) {
      var m = (meta.motions || []).filter(function (x) { return x.name === c.name; })[0];
      if (!m) { this.stage.emit('event', { event: 'error', avatar: this.name, message: 'no motion "' + c.name + '"' }); return; }
      group = m.group; index = m.index;
    }
    var pri = c.priority === 'force' ? 3 : 2, id = c.id;
    this.model.motion(group, index == null ? undefined : index, pri, {
      loop: c.loop ? true : undefined,
      onFinish: function () { self.stage.emit('event', { event: 'motion_end', avatar: self.name, name: c.name || group, id: id }); },
      onError: function (e) { self.stage.emit('event', { event: 'error', avatar: self.name, message: 'motion: ' + (e && e.message || e) }); }
    }).then(function (ok) {
      if (ok) self.stage.emit('event', { event: 'motion_start', avatar: self.name, name: c.name || group, id: id });
    });
  };

  /* -------------------------------------------- speech */

  Avatar.prototype.speak = function (c) {
    if (c.interrupt) this.stopSpeaking();
    this.speech.queue.push(c);
    if (!this.speech.cur) this._nextSpeech();
  };

  Avatar.prototype._nextSpeech = async function () {
    var c = this.speech.queue.shift(), self = this;
    if (!c) { this.speech.cur = null; return; }
    var cur = this.speech.cur = { cmd: c, src: null, stopped: false };
    var stage = this.stage, ctx = stage.audio();
    try {
      var buf = await fetch(c.url).then(function (r) { if (!r.ok) throw new Error('audio ' + r.status); return r.arrayBuffer(); });
      var audio = await ctx.decodeAudioData(buf);
      if (cur.stopped) return;
      var src = ctx.createBufferSource();
      src.buffer = audio;
      var an = ctx.createAnalyser();
      an.fftSize = 2048;
      var delay = ctx.createDelay(1);
      delay.delayTime.value = ((this.cfg.mouth && this.cfg.mouth.delay_ms) || 0) / 1000;
      var gain = ctx.createGain();
      var audible = stage.role === 'obs' ? stage.audioOn : !!stage.hear;     // the preview is silent unless asked
      gain.gain.value = audible ? (c.volume == null ? 1 : c.volume) : 0;
      src.connect(an);
      src.connect(delay);
      delay.connect(gain);
      gain.connect(ctx.destination);
      cur.src = src; cur.nodes = [an, delay, gain];
      if (!this.cfg.mouth || this.cfg.mouth.lipsync !== false) this.lip.attachAnalyser(an);
      src.onended = function () {
        if (self.speech.cur !== cur) return;
        self._endSpeech(cur, cur.stopped ? 'speech_error' : 'speech_end', cur.stopped ? 'stopped' : '');
      };
      src.start();
      cur.started = performance.now();
      stage.emit('event', { event: 'speech_start', avatar: this.name, id: c.id, duration: +audio.duration.toFixed(3), text: c.text || '' });
    } catch (e) {
      if (this.speech.cur === cur) this._endSpeech(cur, 'speech_error', String(e && e.message || e));
    }
  };

  Avatar.prototype._endSpeech = function (cur, ev, why) {
    var self = this;
    this.lip.attachAnalyser(null);
    try { if (cur.src) cur.src.disconnect(); (cur.nodes || []).forEach(function (n) { n.disconnect(); }); } catch (e) {}
    var dur = cur.started ? +((performance.now() - cur.started) / 1000).toFixed(3) : 0;
    this.stage.emit('event', { event: ev, avatar: this.name, id: cur.cmd.id, duration: dur, error: why || undefined });
    this.speech.cur = null;
    setTimeout(function () { self._nextSpeech(); }, 0);
  };

  Avatar.prototype.stopSpeaking = function (quiet) {
    this.speech.queue.length = 0;
    var cur = this.speech.cur;
    if (!cur) return;
    cur.stopped = true;
    if (cur.src) { try { cur.src.stop(); } catch (e) {} }
    else if (!quiet) this._endSpeech(cur, 'speech_error', 'stopped');
  };

  /* -------------------------------------------- items */

  Avatar.prototype._clearItems = function () {
    this.items.forEach(function (it) { it.destroy(); });
    this.items = [];
  };

  Avatar.prototype._syncItems = function () {
    var list = this.cfg.items || [], lib = this.stage.items;
    var sig = JSON.stringify(list.map(function (x) { return [x.id, x.item, !!lib[x.item]]; })) + '|' + (this.model ? this.modelId : '');
    var self = this;
    if (sig !== this.itemSig) {
      this.itemSig = sig;
      this._clearItems();
      list.forEach(function (cfg) { var meta = lib[cfg.item]; if (meta && meta.ok) self.items.push(new Item(self, cfg, meta)); });
    } else {
      list.forEach(function (cfg, i) { var it = self.items.filter(function (x) { return x.cfg.id === cfg.id; })[0]; if (it) it.setConfig(cfg); });
    }
  };

  function Item(av, cfg, meta) {
    this.av = av; this.cfg = cfg; this.meta = meta;
    this.node = new PIXI.Container();
    this.sprite = null;
    this.l2d = null;
    this.ready = false;
    (cfg.layer === 'back' ? av.back : av.front).addChild(this.node);
    this.node.zIndex = cfg.order || 0;
    this._load();
  }

  Item.prototype._load = async function () {
    var meta = this.meta, self = this, disp = null;
    try {
      if (meta.kind === 'image') {
        disp = new PIXI.Sprite(await PIXI.Assets.load(meta.url));
      } else if (meta.kind === 'gif') {
        var res = await PIXI.Assets.load(meta.url);                  // @pixi/gif answers .gif with an AnimatedGIF
        disp = res instanceof PIXI.Texture ? new PIXI.Sprite(res) : (res.clone ? res.clone() : res);
        if (disp.play) disp.play();
      } else if (meta.kind === 'sequence') {
        var tex = await Promise.all(meta.frames.map(function (u) { return PIXI.Assets.load(u); }));
        disp = new PIXI.AnimatedSprite(tex);
        disp.animationSpeed = (this.cfg.fps || meta.fps || 12) / 60;
        disp.play();
      } else if (meta.kind === 'live2d') {
        this.l2d = disp = await L.Live2DModel.from(meta.url, { autoUpdate: false, autoFocus: false, autoHitTest: false });
      }
    } catch (e) {
      this.av.stage.emit('event', { event: 'error', avatar: this.av.name, message: 'item ' + meta.id + ': ' + (e && e.message || e) });
      return;
    }
    if (!disp || this.dead) { if (disp && this.dead) disp.destroy(); return; }
    if (disp.anchor) disp.anchor.set(0.5);
    this.sprite = disp;
    this.node.addChild(disp);
    this.ready = true;
    this.setConfig(this.cfg);
  };

  Item.prototype.setConfig = function (cfg) {
    var layerChanged = cfg.layer !== this.cfg.layer;
    this.cfg = cfg;
    if (layerChanged) (cfg.layer === 'back' ? this.av.back : this.av.front).addChild(this.node);
    this.node.zIndex = cfg.order || 0;
    if (!this.sprite) return;
    var natH = this.l2d ? (this.l2d.internalModel.originalHeight || 1) : (this.sprite.texture ? this.sprite.texture.height : this.sprite.height) || 1;
    // scale 1 = the item is a quarter of the model's height
    var h = H * 0.25 * (cfg.scale || 1);
    var s = h / natH;
    this.sprite.scale.set(s * (cfg.flip ? -1 : 1), s);
    this.node.alpha = cfg.opacity == null ? 1 : cfg.opacity;
    this.node.visible = cfg.visible !== false;
    if (this.sprite.animationSpeed != null && this.meta.kind === 'sequence') this.sprite.animationSpeed = (cfg.fps || 12) / 60;
    this._place();
  };

  Item.prototype._place = function () {
    var cfg = this.cfg, av = this.av, pin = cfg.pin;
    if (pin && av.png && /^layer:/.test(pin.mesh)) {      // pinned to a PNGtuber layer: x, y in its own space
      var gp = av.png.layerPoint(pin.mesh.slice(6), pin.bary[0], pin.bary[1]);
      var ly = av.png.byId[pin.mesh.slice(6)];
      if (gp && ly) {
        var lp2 = av.node.toLocal(gp);
        this.node.position.set(lp2.x, lp2.y);
        this.node.rotation = (cfg.rotation || 0) * Math.PI / 180 + (pin.follow_angle !== false ? Math.atan2(ly.spriteM.b, ly.spriteM.a) : 0);
        return;
      }
    }
    if (pin && av.model && !av.png) {
      try {
        var im = av.model.internalModel, d = im.getDrawableIndex(pin.mesh);
        if (d >= 0) {
          var v = im.getDrawableVertices(d), t = pin.tri, b = pin.bary;
          var x = v[t[0] * 2] * b[0] + v[t[1] * 2] * b[1] + v[t[2] * 2] * b[2];
          var y = v[t[0] * 2 + 1] * b[0] + v[t[1] * 2 + 1] * b[1] + v[t[2] * 2 + 1] * b[2];
          var g = av.model.toGlobal(new PIXI.Point(x, y)), lp = av.node.toLocal(g);
          this.node.position.set(lp.x, lp.y);
          var ang = Math.atan2(v[t[1] * 2 + 1] - v[t[0] * 2 + 1], v[t[1] * 2] - v[t[0] * 2]);
          this.node.rotation = (cfg.rotation || 0) * Math.PI / 180 + (pin.follow_angle !== false ? ang - (pin.angle0 || 0) : 0);
          return;
        }
      } catch (e) {}
    }
    // not pinned: x / y are % of the model's own box (0,0 its top left, 50,50 its centre)
    var bw = av.boxWidth();
    this.node.position.set((cfg.x - 50) / 100 * bw, (cfg.y - 50) / 100 * H);
    this.node.rotation = (cfg.rotation || 0) * Math.PI / 180;
  };

  Item.prototype.tick = function (dt) {
    if (!this.ready) return;
    if (this.l2d) this.l2d.update(dt * 1000);
    if (this.cfg.pin) this._place();
  };

  Item.prototype.destroy = function () {
    this.dead = true;
    try { this.node.destroy({ children: true }); } catch (e) {}
  };

  HA.Item = Item;

  /* -------------------------------------------- PNGtubers */

  // 2D affine matrices {a, b, c, d, tx, ty} (Pixi's layout): p * q applies q first
  function mmul(p, q) {
    return { a: p.a * q.a + p.c * q.b, b: p.b * q.a + p.d * q.b, c: p.a * q.c + p.c * q.d, d: p.b * q.c + p.d * q.d,
             tx: p.a * q.tx + p.c * q.ty + p.tx, ty: p.b * q.tx + p.d * q.ty + p.ty };
  }
  function mT(x, y) { return { a: 1, b: 0, c: 0, d: 1, tx: x, ty: y }; }
  var M_ID = mT(0, 0);
  function mApply(m, x, y) { return { x: m.a * x + m.c * y + m.tx, y: m.b * x + m.d * y + m.ty }; }
  function mInv(m) {
    var det = m.a * m.d - m.b * m.c || 1e-9;
    return { a: m.d / det, b: -m.b / det, c: -m.c / det, d: m.a / det, tx: (m.c * m.ty - m.d * m.tx) / det, ty: (m.b * m.tx - m.a * m.ty) / det };
  }

  /* a simple rig (pictures per state) as layers, with the show-when rules of each picture */
  function simpleLayers(rig) {
    var out = [];
    Object.keys(rig.states || {}).forEach(function (st) {
      var s = rig.states[st];
      var talks = !!(s.talk || s.talk_blink || s.half || s.A || s.I || s.U || s.E || s.O);
      function add(role, talk, blink, vowel) {
        if (!s[role]) return;
        out.push({ id: st + ':' + role, name: st + ' ' + role, image: s[role], parent: null, x: 0, y: 0, ox: 0, oy: 0, z: 0,
                   talk: talk, blink: blink, vowel: vowel || '', states: [st], wobble: [0, 0, 0, 0], drag: 0, rot_drag: 0,
                   rot_min: -180, rot_max: 180, stretch: 0, ignore_bounce: false, frames: 1, fps: 0, clip: false, toggle: '' });
      }
      add('idle', talks ? 1 : 0, s.blink ? 1 : 0);
      add('blink', talks ? 1 : 0, 2);
      add('talk', 2, s.talk_blink ? 1 : 0);
      add('talk_blink', 2, 2);
      add('half', 3, 0);
      ['A', 'I', 'U', 'E', 'O'].forEach(function (v) { add(v, 2, 0, v); });
    });
    return out;
  }

  function PngRig(av, rig) {
    this.av = av;
    this.rig = rig;
    this.root = new PIXI.Container();        // the avatar's holder holds this: bounce, head motion, breathing
    this.root.label = 'pngtuber';
    this.draw = new PIXI.Container();
    this.draw.sortableChildren = true;
    this.root.addChild(this.draw);
    var src = rig.style === 'simple' ? simpleLayers(rig) : (rig.layers || []);
    this.layers = src.map(function (l) { return Object.assign({}, l, { s: { dx: null, dy: null, rot: 0, sx: 1, sy: 1, ft: 0 } }); });
    this.byId = {};
    var self = this;
    this.layers.forEach(function (l) { self.byId[l.id] = l; });
    this.parts = { ids: this.layers.map(function (l) { return l.id; }), index: {} };
    this.layers.forEach(function (l, i) { self.parts.index[l.id] = i; });
    this.state = rig.default_state;
    this.talking = false; this.soft = false; this.blinking = false; this.vowel = ''; this.level = 0;
    this.hold = 0; this.by = 0; this.vy = 0; this.t = 0;
    // per state: does it have a half-open mouth / vowel mouths (they take over from the plain one)
    this.stateInfo = {};
    this.layers.forEach(function (l) {
      (l.states && l.states.length ? l.states : ['*']).forEach(function (st) {
        var si = self.stateInfo[st] = self.stateInfo[st] || { half: false, vowels: {} };
        if (l.talk === 3) si.half = true;
        if (l.vowel) si.vowels[l.vowel] = true;
      });
    });
  }

  PngRig.prototype.load = async function (base) {
    var tex = {}, self = this, list = {};
    this.layers.forEach(function (l) { if (l.image) list[l.image] = 1; });
    await Promise.all(Object.keys(list).map(function (img) {
      var url = base + img.split('/').map(encodeURIComponent).join('/');
      return PIXI.Assets.load(url).then(function (t) { tex[img] = t; }).catch(function () { tex[img] = null; });
    }));
    // draw order: depth relative to the parent (as PNGTuber Plus / Godot), ties in tree order
    var order = 0, kids = {};
    this.layers.forEach(function (l) { (kids[l.parent || ''] = kids[l.parent || ''] || []).push(l); });
    (function walk(parent, zBase) {
      (kids[parent] || []).forEach(function (l) { l.zEff = zBase + (l.z || 0); l.order = order++; walk(l.id, l.zEff); });
    })('', 0);
    this.layers.forEach(function (l) { if (l.order == null) { l.zEff = l.z || 0; l.order = order++; } });
    this.layers.sort(function (a, b) { return a.order - b.order; });           // parents before their children
    this.layers.forEach(function (l) {
      var t = tex[l.image];
      if (!t) return;
      var sp;
      if (!(t instanceof PIXI.Texture) && t.clone) { sp = t.clone(); if (sp.play) sp.play(); }        // an animated GIF
      else if (l.frames > 1) {
        var fw = Math.floor(t.width / l.frames);
        l.frameTex = [];
        for (var i = 0; i < l.frames; i++) l.frameTex.push(new PIXI.Texture({ source: t.source, frame: new PIXI.Rectangle(i * fw, 0, fw, t.height) }));
        sp = new PIXI.Sprite(l.frameTex[0]);
      } else sp = new PIXI.Sprite(t);
      sp.anchor.set(0.5);
      sp.zIndex = l.zEff * 10000 + l.order;
      l.sprite = sp;
      self.draw.addChild(sp);
    });
    // clipping: a layer marked "clip" shows its children only where it is opaque
    this.layers.forEach(function (l) {
      if (!l.clip || !l.sprite) return;
      var mask = new PIXI.Sprite(l.sprite.texture);
      mask.anchor.set(0.5);
      l.mask = mask;
      self.draw.addChild(mask);
      (function walk(id) { (kids[id] || []).forEach(function (c) { if (c.sprite) c.sprite.mask = mask; walk(c.id); }); })(l.id);
    });
    // the box at rest (the default state, quiet, eyes open): what the avatar is sized and centred by
    this._pose(0, true);
    var box = null;
    function grow(l, onlyShown) {
      if (!l.sprite || (onlyShown && !l.sprite.visible)) return;
      var w = l.sprite.texture.width / 2, h = l.sprite.texture.height / 2, m = l.imgM;
      [[-w, -h], [w, -h], [w, h], [-w, h]].forEach(function (p) {
        var q = mApply(m, p[0], p[1]);
        box = box ? { x0: Math.min(box.x0, q.x), y0: Math.min(box.y0, q.y), x1: Math.max(box.x1, q.x), y1: Math.max(box.y1, q.y) } : { x0: q.x, y0: q.y, x1: q.x, y1: q.y };
      });
    }
    this.layers.forEach(function (l) { grow(l, true); });
    if (!box) this.layers.forEach(function (l) { grow(l, false); });
    box = box || { x0: -50, y0: -50, x1: 50, y1: 50 };
    this.box = { x: box.x0, y: box.y0, w: Math.max(1, box.x1 - box.x0), h: Math.max(1, box.y1 - box.y0) };
    this.root.pivot.set(this.box.x + this.box.w / 2, this.box.y + this.box.h / 2);
    if (!this.layers.some(function (l) { return l.sprite; })) throw new Error('none of its pictures could be loaded');
  };

  PngRig.prototype.setState = function (name) {
    if (name && (this.rig.style === 'simple' ? this.rig.states[name] : (this.rig.states || []).indexOf(name) >= 0)) this.state = name;
    else this.state = this.rig.default_state;
  };

  PngRig.prototype.hop = function (amount) { if (this.by > -16) this.vy = -this.rig.bounce * (amount || 1); };

  /* is this layer shown right now */
  PngRig.prototype._shown = function (l) {
    if (l.states && l.states.length && l.states.indexOf(this.state) < 0) return false;
    var si = this.stateInfo[this.state] || this.stateInfo['*'] || { half: false, vowels: {} }, t = this.talking;
    switch (l.talk) {
      case 1: if (t) return false; break;
      case 2:
        if (!t || (this.soft && si.half)) return false;
        if (l.vowel) { if (this.vowel !== l.vowel) return false; }
        else if (this.vowel && si.vowels[this.vowel]) return false;
        break;
      case 3: if (!t || !this.soft) return false; break;
    }
    if (l.blink === 1 && this.blinking) return false;
    if (l.blink === 2 && !this.blinking) return false;
    return true;
  };

  /* lay out every layer for this frame (rest = no motion, for measuring) */
  PngRig.prototype._pose = function (dt, rest) {
    var t60 = this.t * 60, k = dt > 0 ? Math.min(4, dt * 60) : 1, ov = this.av.partOv || {};
    for (var i = 0; i < this.layers.length; i++) {
      var l = this.layers[i], s = l.s;
      var parent = l.parent && this.byId[l.parent];
      var pm = parent && parent.spriteM ? parent.spriteM : M_ID;
      var wob = rest ? [0, 0] : [Math.sin(t60 * l.wobble[1]) * l.wobble[0], Math.sin(t60 * l.wobble[3]) * l.wobble[2]];
      var target = mmul(pm, mT(l.x + wob[0], l.y + wob[1]));
      // follow-lag ("drag"): the layer trails behind where it should be
      var prevY = s.dy;
      if (rest || s.dx == null || !l.drag) { s.dx = target.tx; s.dy = target.ty; }
      else { var f = 1 - Math.pow(1 - 1 / Math.max(1, l.drag), k); s.dx += (target.tx - s.dx) * f; s.dy += (target.ty - s.dy) * f; }
      if (!rest && prevY != null && (l.rot_drag || l.stretch)) {
        var len = (prevY - (l.ignore_bounce ? this.bounceChange : 0) - s.dy) / k;       // upward movement per 1/60 s
        var rt = clamp(len * l.rot_drag, l.rot_min, l.rot_max) * Math.PI / 180;
        s.rot += (rt - s.rot) * (1 - Math.pow(0.75, k));
        var st = len * l.stretch * 0.01;
        s.sx += (1 - st - s.sx) * (1 - Math.pow(0.5, k));
        s.sy += (1 + st - s.sy) * (1 - Math.pow(0.5, k));
      }
      var c = Math.cos(s.rot), sn = Math.sin(s.rot);
      var local = { a: c * s.sx, b: sn * s.sx, c: -sn * s.sy, d: c * s.sy, tx: 0, ty: 0 };
      var lin = mmul({ a: target.a, b: target.b, c: target.c, d: target.d, tx: 0, ty: 0 }, local);
      l.spriteM = { a: lin.a, b: lin.b, c: lin.c, d: lin.d, tx: s.dx, ty: s.dy };
      l.imgM = mmul(l.spriteM, mT(l.ox, l.oy));
      if (!l.sprite) continue;
      var shown = this._shown(l), o = ov[l.id];
      l.sprite.visible = shown && !(o && o.w > 0.5 && o.value <= 0);
      l.sprite.alpha = o && o.w > 0 ? lerp(1, o.value, o.w) : 1;
      l.sprite.setFromMatrix(new PIXI.Matrix(l.imgM.a, l.imgM.b, l.imgM.c, l.imgM.d, l.imgM.tx, l.imgM.ty));
      if (l.mask) l.mask.setFromMatrix(new PIXI.Matrix(l.imgM.a, l.imgM.b, l.imgM.c, l.imgM.d, l.imgM.tx, l.imgM.ty));
      if (l.frameTex && l.fps > 0 && !rest) {
        s.ft += dt * l.fps;
        l.sprite.texture = l.frameTex[Math.floor(s.ft) % l.frameTex.length];
      }
    }
  };

  PngRig.prototype.update = function (dt, F, now) {
    var r = this.rig, av = this.av, lip = (F && F.lip) || {};
    // talking: the voice (or the bot's MouthOpen / mouth_open), with a short hold so words don't flicker
    var level = Math.max(lip.level || 0, av.inputOv('MouthOpen', F ? F.MouthOpen : 0));
    this.level = level;
    if (level > r.threshold) this.hold = r.hold; else this.hold = Math.max(0, this.hold - dt);
    var was = this.talking;
    this.talking = this.hold > 0;
    this.soft = this.talking && level < r.threshold * 2.4;
    var best = '', bv = 0.3;
    if (this.talking && lip.level > 0.02) ['A', 'I', 'U', 'E', 'O'].forEach(function (v) { if ((lip[v] || 0) > bv) { bv = lip[v]; best = v; } });
    this.vowel = best;
    var eyes = Math.min(av.inputOv('EyeOpenLeft', F ? F.eyeL : 1), av.inputOv('EyeOpenRight', F ? F.eyeR : 1));
    this.blinking = eyes < 0.4;
    // the bounce when speech starts (PNGTuber Plus: up at `bounce` px/s, falling at `gravity` px/s2)
    var sm = ((av.cfg.idle && av.cfg.idle.speech_motion) == null ? 50 : av.cfg.idle.speech_motion) / 50;
    if (this.talking && !was && this.by > -16 && r.bounce > 0) this.vy = -r.bounce * sm;
    var prev = this.by;
    this.by += this.vy * dt;
    this.vy += r.gravity * dt;
    if (this.by >= 0) { this.by = 0; this.vy = Math.min(this.vy, 0); }
    this.bounceChange = prev - this.by;
    // the tracker's head motion as whole-picture motion: turn -> shift, nod -> bob, tilt -> lean
    var u = this.box.h / 1000;
    var fx = av.inputOv('FaceAngleX', F ? F.FaceAngleX : 0, true), fy = av.inputOv('FaceAngleY', F ? F.FaceAngleY : 0, true);
    var fz = av.inputOv('FaceAngleZ', F ? F.FaceAngleZ : 0, true);
    var breathe = r.breathe ? Math.sin(now * Math.PI * 2 / 3.6) * 0.006 : 0;
    this.root.position.set(fx * 1.4 * u, -fy * 1.6 * u + this.by * 1);
    this.root.rotation = -fz * 0.6 * Math.PI / 180;
    this.root.scale.set(av.baseScale * (1 - breathe * 0.4), av.baseScale * (1 + breathe));
    this.root.position.y *= av.baseScale;
    this.root.position.x *= av.baseScale;
    this.t += dt;
    this._pose(dt, false);
  };

  PngRig.prototype.hit = function (gx, gy) {
    for (var i = 0; i < this.layers.length; i++) {
      var sp = this.layers[i].sprite;
      if (!sp || !sp.visible) continue;
      var b = sp.getBounds();
      if (gx >= b.x && gy >= b.y && gx <= b.x + b.width && gy <= b.y + b.height) return true;
    }
    return false;
  };

  /* the topmost shown layer under a rig-space point, with the point in that layer's own space */
  PngRig.prototype.layerAt = function (x, y) {
    var list = this.layers.filter(function (l) { return l.sprite && l.sprite.visible; })
      .sort(function (a, b) { return b.sprite.zIndex - a.sprite.zIndex; });
    for (var i = 0; i < list.length; i++) {
      var l = list[i], p = mApply(mInv(l.imgM), x, y), w = l.sprite.texture.width / 2, h = l.sprite.texture.height / 2;
      if (p.x >= -w && p.x <= w && p.y >= -h && p.y <= h) { var q = mApply(mInv(l.spriteM), x, y); return { id: l.id, lx: q.x, ly: q.y }; }
    }
    return null;
  };

  PngRig.prototype.layerPoint = function (id, lx, ly) {
    var l = this.byId[id];
    if (!l || !l.spriteM) return null;
    return this.draw.toGlobal(new PIXI.Point(mApply(l.spriteM, lx, ly).x, mApply(l.spriteM, lx, ly).y));
  };

  PngRig.prototype.destroy = function () { try { this.root.destroy({ children: true }); } catch (e) {} };

  HA.PngRig = PngRig;

  /* -------------------------------------------- light */

  // Pixi's filter vertex shader, plus the position inside the filter area and the texel size as
  // varyings (a uniform used in both stages must match in precision, which fragment shaders don't)
  var LIGHT_VERT = [
    'in vec2 aPosition;',
    'out vec2 vTextureCoord;',
    'out vec2 vPos;',
    'out vec2 vTexel;',
    'uniform vec4 uInputSize;',
    'uniform vec4 uOutputFrame;',
    'uniform vec4 uOutputTexture;',
    'void main(void){',
    '  vec2 position = aPosition * uOutputFrame.zw + uOutputFrame.xy;',
    '  position.x = position.x * (2.0 / uOutputTexture.x) - 1.0;',
    '  position.y = position.y * (2.0 * uOutputTexture.z / uOutputTexture.y) - uOutputTexture.z;',
    '  gl_Position = vec4(position, 0.0, 1.0);',
    '  vTextureCoord = aPosition * (uOutputFrame.zw * uInputSize.zw);',
    '  vPos = aPosition;',
    '  vTexel = uInputSize.zw;',
    '}'
  ].join('\n');
  var LIGHT_FRAG = [
    'in vec2 vTextureCoord;',
    'in vec2 vPos;',
    'in vec2 vTexel;',
    'out vec4 finalColor;',
    'uniform sampler2D uTexture;',
    'uniform vec3 uKey;',
    'uniform float uKeyAmt;',
    'uniform vec2 uDir;',
    'uniform vec3 uAmb;',
    'uniform float uAmbAmt;',
    'uniform vec3 uRim;',
    'uniform float uRimAmt;',
    'uniform float uFlash;',
    'void main(void){',
    '  vec4 c = texture(uTexture, vTextureCoord);',
    '  if (c.a < 0.004) { finalColor = c; return; }',
    '  vec3 rgb = c.rgb / c.a;',
    '  vec2 p = vPos - 0.5;',
    '  rgb = mix(rgb, rgb * uAmb, uAmbAmt);',
    '  float g = clamp(dot(p, uDir) * 1.6 + 0.35, 0.0, 1.0);',
    '  rgb = rgb * (1.0 - uKeyAmt * 0.35) + uKey * uKeyAmt * g * 0.75 * (0.35 + rgb);',
    '  vec2 px = vTexel * 10.0;',
    '  float ahead = texture(uTexture, vTextureCoord + uDir * px).a;',
    '  float rim = clamp(c.a - ahead, 0.0, 1.0);',
    '  rgb += uRim * rim * uRimAmt;',
    '  rgb += vec3(uFlash);',
    '  finalColor = vec4(clamp(rgb, 0.0, 1.0) * c.a, c.a);',
    '}'
  ].join('\n');

  Avatar.prototype.setLight = function (lt, fade) {
    this.lightCfg = lt && lt.enabled ? lt : null;
    this.lightFade = Math.max(0.01, fade == null ? 0.5 : fade);
    if (this.lightCfg && !this.light) {
      try {
        this.lightU = new PIXI.UniformGroup({
          uKey: { value: new Float32Array([1, 0.9, 0.7]), type: 'vec3<f32>' }, uKeyAmt: { value: 0, type: 'f32' },
          uDir: { value: new Float32Array([-0.7, -0.7]), type: 'vec2<f32>' },
          uAmb: { value: new Float32Array([1, 1, 1]), type: 'vec3<f32>' }, uAmbAmt: { value: 0, type: 'f32' },
          uRim: { value: new Float32Array([0.5, 0.7, 1]), type: 'vec3<f32>' }, uRimAmt: { value: 0, type: 'f32' },
          uFlash: { value: 0, type: 'f32' }
        });
        this.light = new PIXI.Filter({ glProgram: PIXI.GlProgram.from({ vertex: LIGHT_VERT, fragment: LIGHT_FRAG, name: 'hexcast-light' }),
                                       resources: { lightUniforms: this.lightU } });
        this.light.padding = 12;
        this.node.filters = [this.light];
      } catch (e) { console.warn('light filter', e); this.light = null; }
    }
  };

  Avatar.prototype._lightTick = function (dt, now) {
    if (!this.light) return;
    var on = !!this.lightCfg;
    this.lightW = approach(this.lightW, on ? 1 : 0, dt, this.lightFade / 3);
    if (!on && this.lightW < 0.004) { this.node.filters = null; this.light.destroy(); this.light = null; this.lightW = 0; return; }
    var lt = this.lightCfg || this._lastLight || {}, w = this.lightW;
    if (on) this._lastLight = lt;
    var key = hexRgb(lt.color), amb = hexRgb(lt.ambient), rim = hexRgb(lt.rim);
    var ki = lt.intensity || 0, ri = lt.rim_amount || 0, ai = lt.ambient_amount || 0, ang = (lt.angle || 0) * Math.PI / 180;
    var sp = lt.speed || 1, t = now * sp, flash = 0, seed = this.seed;
    switch (lt.preset) {
      case 'pulse': ki *= 0.55 + 0.45 * Math.sin(t * 2.4); ri *= 0.6 + 0.4 * Math.sin(t * 2.4); break;
      case 'flicker': ki *= 0.75 + 0.25 * noise(seed, t * 9); break;
      case 'fire': key = [1, 0.48 + 0.12 * noise(seed, t * 5), 0.12]; ki *= 0.7 + 0.35 * fbm(seed, t * 7); ang = Math.PI / 2 + noise(seed + 5, t * 3) * 0.3; break;
      case 'police': var ph = Math.floor(t * 3) % 2; key = ph ? [1, 0.12, 0.12] : [0.15, 0.3, 1]; ang = ph ? 0 : Math.PI; rim = ph ? [0.2, 0.35, 1] : [1, 0.15, 0.15]; ri = Math.max(ri, 0.8); break;
      case 'rainbow': key = hsv((t * 0.1) % 1, 0.75, 1); rim = hsv((t * 0.1 + 0.5) % 1, 0.8, 1); break;
      case 'strobe': flash = (Math.floor(t * 8) % 2) ? 0.35 : 0; break;
      case 'lightning': var r = hash(seed, Math.floor(t * 4)); flash = r > 0.93 ? 0.55 * (1 - (t * 4 % 1)) : 0; rim = [0.7, 0.8, 1]; ri = Math.max(ri, flash * 2); break;
      case 'neon': var pn = 0.6 + 0.4 * Math.sin(t * 1.7); key = [1, 0.25, 0.75]; rim = [0.2, 0.95, 1]; ki *= pn; ri = Math.max(ri, 0.9 * (1.4 - pn)); break;
    }
    var u = this.lightU.uniforms;
    u.uKey[0] = key[0]; u.uKey[1] = key[1]; u.uKey[2] = key[2];
    u.uKeyAmt = ki * w;
    // the light comes from `angle` (0 = from the right, -90 = from above): the lit side faces it
    u.uDir[0] = Math.cos(ang); u.uDir[1] = Math.sin(ang);
    u.uAmb[0] = amb[0]; u.uAmb[1] = amb[1]; u.uAmb[2] = amb[2];
    u.uAmbAmt = ai * w;
    u.uRim[0] = rim[0]; u.uRim[1] = rim[1]; u.uRim[2] = rim[2];
    u.uRimAmt = ri * w;
    u.uFlash = flash * w;
  };

  /* -------------------------------------------- what the API can ask for */

  Avatar.prototype.paramValues = function () {
    if (!this.model) return {};
    if (this.png) {
      var pr = this.png;
      return { state: pr.state, talking: pr.talking, blinking: pr.blinking, vowel: pr.vowel, level: +pr.level.toFixed(3) };
    }
    var cm = this.model.internalModel.coreModel, out = {};
    for (var i = 0; i < this.P.ids.length; i++) out[this.P.ids[i]] = +cm.getParameterValueByIndex(i).toFixed(4);
    return out;
  };

  HA.Avatar = Avatar;
  HA.util = { clamp: clamp, ease: ease, hash: hash, noise: noise };
  HA.W = W; HA.H = H;
})();
