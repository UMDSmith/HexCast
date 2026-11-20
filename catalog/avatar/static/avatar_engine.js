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
    this.backLayer = new PIXI.Container();         // backgrounds: behind every avatar of the overlay
    this.backLayer.label = 'backgrounds';
    this.backLayer.sortableChildren = true;
    this.backLayer.zIndex = -1e6;
    this.root.addChild(this.backLayer);
    this.frameNo = 0;
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
    this._mounts();
    if (this.selected && !this.avatars[this.selected]) this.select(null);
    this._sendHave();
  };

  /* who hangs from whom (`attach` in the config): link each avatar to its parent when that is drawn here and the chain is no
     loop; update parents before the avatars that hang from them (a child is placed from where its parent is now); and draw a
     child just over (or under) its parent. `drawOrder` is the stacking, bottom to top, for picking. */
  Stage.prototype._mounts = function () {
    var self = this, want = {}, n, cur, hops;
    for (n in this.avatars) {
      var at = this.avatars[n].cfg.attach;
      if (at && at.to !== n && this.avatars[at.to]) want[n] = at.to;
    }
    for (n in this.avatars) {
      cur = want[n]; hops = 0;
      while (cur && cur !== n && hops++ < 9) cur = want[cur];
      this.avatars[n].mountParent = want[n] && cur !== n && hops < 9 ? this.avatars[want[n]] : null;
    }
    var seen = {}, upd = [];
    function visit(a) { if (!a || seen[a.name]) return; seen[a.name] = 1; visit(a.mountParent); upd.push(a.name); }
    this.order.forEach(function (nm) { visit(self.avatars[nm]); });
    upd.forEach(function (nm) {                           // (a step of 0.1, half of that one level further down: never as much as a whole place in the list)
      var a = self.avatars[nm], p = a.mountParent;
      a.mountDepth = p ? p.mountDepth + 1 : 0;
      if (p) a.node.zIndex = p.node.zIndex + (a.cfg.attach.layer === 'back' ? -1 : 1) * 0.1 / Math.pow(2, a.mountDepth - 1);
    });
    this.updateOrder = upd;
    this.drawOrder = this.order.slice().sort(function (x, y) { return self.avatars[x].node.zIndex - self.avatars[y].node.zIndex; });
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
    this.frameNo++;
    var order = this.updateOrder || this.order;
    for (var i = 0; i < order.length; i++) {
      var av = this.avatars[order[i]];
      if (av) av.update(dt, now);
    }
    this._clipsTick();
    if (this.editable) this._drawSelection();
  };

  Stage.prototype.destroy = function () {
    this._clipOff = true;
    this.stopClips();
    if (this._clipWs) { try { this._clipWs.close(); } catch (e) {} this._clipWs = null; }
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

  /* ------------------------------------------------------------------ Spout output */

  /* A hidden browser that Hexcast starts draws the overlay for Spout (Shoost, VTube Studio's background ...): the stage is
     drawn at its own 1920x1080 into a texture instead of the page, turned into what Spout shares (B, G, R, A with straight
     alpha, the top row first), and read back without stalling the graphics card (the read finishes while the next frame is
     drawn: a frame is handed over one or two frames after it was drawn). `send(bytes, width, height)` gets each frame. */
  var SPOUT_FRAG = [
    'in vec2 vTextureCoord;',
    'out vec4 finalColor;',
    'uniform sampler2D uTexture;',
    'void main(void){',
    '  vec4 c = texture(uTexture, vTextureCoord);',
    '  finalColor = c.a > 0.0 ? vec4(c.b / c.a, c.g / c.a, c.r / c.a, c.a) : vec4(0.0);',
    '}'
  ].join('\n');

  Stage.prototype.startCapture = function (opts) {
    var app = this.app, r = app.renderer, gl = r.gl, self = this;
    if (typeof WebGL2RenderingContext === 'undefined' || !(gl instanceof WebGL2RenderingContext)) throw new Error('Spout output needs WebGL 2');
    var cap = this.cap = { fps: Math.max(1, opts.fps || 30), send: opts.send, ready: opts.ready || function () { return true; },
                           target: opts.target || null, last: 0, w: W, h: H, pbo: [], fence: [null, null], next: 0,
                           buf: new Uint8Array(W * H * 4), frames: 0 };
    cap.rt = PIXI.RenderTexture.create({ width: W, height: H, resolution: 1, antialias: true });
    cap.out = PIXI.RenderTexture.create({ width: W, height: H, resolution: 1 });
    cap.sprite = new PIXI.Sprite(cap.rt);
    cap.sprite.filters = [new PIXI.Filter({ glProgram: PIXI.GlProgram.from({ vertex: LOOK_VERT, fragment: SPOUT_FRAG, name: 'hexcast-spout' }),
                                            resources: {}, resolution: 1 })];
    for (var i = 0; i < 2; i++) {
      cap.pbo[i] = gl.createBuffer();
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, cap.pbo[i]);
      gl.bufferData(gl.PIXEL_PACK_BUFFER, W * H * 4, gl.STREAM_READ);
    }
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
    // the stage at its own size whatever the page is, and the page itself draws nothing
    this.fit = function () { self.scale = 1; if (self.root) { self.root.scale.set(1); self.root.position.set(0, 0); } };
    this.fit();
    app.ticker.remove(app.render, app);
    app.ticker.add(this._captureTick, this, PIXI.UPDATE_PRIORITY.LOW);
  };

  Stage.prototype._captureTick = function () {
    var cap = this.cap, r = this.app.renderer, gl = r.gl, k, j;
    for (k = 0; k < 2; k++) {                                // the frames read back before: hand over those the GPU has finished, oldest first
      j = (cap.next + k) % 2;
      var f = cap.fence[j];
      if (!f) continue;
      var st = gl.clientWaitSync(f, 0, 0);
      if (st === gl.TIMEOUT_EXPIRED) break;
      gl.deleteSync(f);
      cap.fence[j] = null;
      if ((st === gl.ALREADY_SIGNALED || st === gl.CONDITION_SATISFIED) && cap.ready()) {     // (the socket still busy: this frame is skipped)
        var into = cap.target ? cap.target() : cap.buf;
        gl.bindBuffer(gl.PIXEL_PACK_BUFFER, cap.pbo[j]);
        gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, into);
        gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
        cap.frames++;
        try { cap.send(into, cap.w, cap.h); } catch (e) { console.warn('spout send', e); }
      }
    }
    var now = performance.now(), period = 1000 / cap.fps;
    if (now < cap.last - 3) return;                          // (cap.last: when the next frame is due - kept on schedule, not by the last one)
    cap.last = Math.max(cap.last + period, now - period / 2);
    var i = cap.next;
    if (cap.fence[i]) { gl.deleteSync(cap.fence[i]); cap.fence[i] = null; }     // (never read: a newer one takes its place)
    r.render({ container: this.app.stage, target: cap.rt, clear: true });
    r.render({ container: cap.sprite, target: cap.out, clear: true });
    r.renderTarget.bind(cap.out, false);
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, cap.pbo[i]);
    gl.readPixels(0, 0, cap.w, cap.h, gl.RGBA, gl.UNSIGNED_BYTE, 0);
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
    cap.fence[i] = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    gl.flush();
    cap.next = 1 - i;
  };

  /* What goes over the socket: after a first whole frame, only what changed since the last frame sent - the frame is cut into
     bands of rows, each band's changed columns found, and neighbouring bands with about the same columns merged into one
     rectangle. A message: "HXS1", the number of rectangles (u16) and 0 (u16), then x, y, w, h (u16 each) per rectangle, then
     each rectangle's pixels (w x h x 4 bytes, rows top first), all little-endian. Nothing changed: no message. The packer owns
     two frames - the one being read into (target) and the last one sent - so nothing is copied but what is sent. */
  function SpoutPacker(w, h, band) {
    this.w = w; this.h = h; this.band = band || 24;
    this.work = new Uint8Array(w * h * 4);
    this.sent = new Uint8Array(w * h * 4);
    this.out = new ArrayBuffer(8 + 8 * Math.ceil(h / this.band) + w * h * 4);
    this.full = true;
  }
  SpoutPacker.prototype.target = function () { return this.work; };
  SpoutPacker.prototype.forceFull = function () { this.full = true; };
  SpoutPacker.prototype.rects = function () {
    var w = this.w, h = this.h, cur = new Uint32Array(this.work.buffer), old = new Uint32Array(this.sent.buffer), out = [];
    if (this.full) return [[0, 0, w, h]];
    for (var y0 = 0; y0 < h; y0 += this.band) {
      var y1 = Math.min(h, y0 + this.band), lo = w, hi = -1;
      for (var y = y0; y < y1; y++) {
        var o = y * w, x;
        for (x = 0; x < lo; x++) if (cur[o + x] !== old[o + x]) { lo = x; break; }
        if (hi < 0 && x >= w) continue;                             // (nothing in this row)
        for (x = w - 1; x > hi; x--) if (cur[o + x] !== old[o + x]) { hi = x; break; }
      }
      if (hi < 0) continue;
      var last = out[out.length - 1];
      if (last && last[1] + last[3] === y0) {                       // the band right under the last rectangle: one rectangle, if they are alike
        var ux0 = Math.min(last[0], lo), ux1 = Math.max(last[0] + last[2], hi + 1);
        if (ux1 - ux0 <= 1.3 * Math.max(last[2], hi + 1 - lo)) { last[0] = ux0; last[2] = ux1 - ux0; last[3] = y1 - last[1]; continue; }
      }
      out.push([lo, y0, hi + 1 - lo, y1 - y0]);
    }
    return out;
  };
  /* the message for the frame now in target(), or null when nothing changed; a message makes it the last frame sent */
  SpoutPacker.prototype.pack = function () {
    var rects = this.rects();
    if (!rects.length) return null;
    var dv = new DataView(this.out), u8 = new Uint8Array(this.out), w = this.w, p = 8 + 8 * rects.length, i, k;
    dv.setUint8(0, 72); dv.setUint8(1, 88); dv.setUint8(2, 83); dv.setUint8(3, 49);              // "HXS1"
    dv.setUint16(4, rects.length, true); dv.setUint16(6, 0, true);
    for (i = 0; i < rects.length; i++) {
      var r = rects[i];
      for (k = 0; k < 4; k++) dv.setUint16(8 + i * 8 + k * 2, r[k], true);
      for (var y = r[1]; y < r[1] + r[3]; y++) {
        var a = (y * w + r[0]) * 4, n = r[2] * 4;
        u8.set(this.work.subarray(a, a + n), p);
        p += n;
      }
    }
    var t = this.sent; this.sent = this.work; this.work = t;
    this.full = false;
    return new Uint8Array(this.out, 0, p);
  };
  HA.SpoutPacker = SpoutPacker;

  /* ------------------------------------------------------------------ soundboard clips glued to an avatar */

  /* The soundboard (Hexcast's own /overlay) plays a clip where its settings say. A clip locked to an avatar plays here instead,
     on the overlay that draws that avatar: Hexcast sends it down the soundboard socket (role=avatar) and it rides the avatar's
     pin point the way an item does - as a page layer over the art (or under it), so keying, trim and fade work as in the
     soundboard. Its sound comes out of this source (silent in the tab's preview). */
  var CLIP_STILL_MS = 4000, CLIP_ERR = { 1: 'aborted', 2: 'network error', 3: 'decode failed', 4: 'source not supported' };

  Stage.prototype._clipLayer = function (front) {
    var key = front ? '_clipFront' : '_clipBack';
    if (!this[key]) {
      var m = this.mount, cv = this.app.canvas, d = document.createElement('div');
      if (getComputedStyle(m).position === 'static') m.style.position = 'relative';
      cv.style.position = 'relative'; cv.style.zIndex = '1';
      d.style.cssText = 'position:absolute;left:0;top:0;right:0;bottom:0;overflow:hidden;pointer-events:none;z-index:' + (front ? 2 : 0);
      if (front) m.appendChild(d); else m.insertBefore(d, cv);
      this[key] = d;
    }
    return this[key];
  };

  Stage.prototype._clipError = function (message, url) {
    console.error('clip: ' + message, url || '');
    if (this._clipWs && this._clipWs.readyState === 1) this._clipWs.send(JSON.stringify({ type: 'error', message: message, url: url }));
  };

  /* play a clip (a soundboard `video` message with `attach`) on the avatar it is locked to; false if that avatar is not drawn here */
  Stage.prototype.playClip = function (m) {
    var at = m.attach, av = at && this.avatars[String(at.avatar || '').toLowerCase()];
    if (!av || !this.app) return false;
    var self = this, url = m.url, isVideo = /\.(mp4|webm|mov|mkv)$/i.test(url);
    var el = document.createElement(isVideo ? 'video' : 'img'), visual = el, keyer = null;
    el.src = url;
    if (m.chroma && window.HexChroma) {
      keyer = HexChroma.attach(el, m.chroma);
      if (keyer) {
        visual = keyer.canvas;
        if (isVideo) Object.assign(el.style, { position: 'fixed', left: '-99999px', top: '0' });      // (kept decoding, off screen)
      }
    }
    Object.assign(visual.style, { position: 'absolute', left: '0', top: '0', transformOrigin: '0 0', maxWidth: 'none', maxHeight: 'none',
                                  visibility: 'hidden', filter: 'drop-shadow(0 8px 24px rgba(0,0,0,0.5))' });
    var clip = { visual: visual, el: el, keyer: keyer, avatar: av.name, done: false,
                 at: Object.assign({ x: 50, y: 20, dx: 0, dy: 0, scale: 1, rotation: 0, follow_angle: true, mirror: false }, at) };
    (this.clips = this.clips || []).push(clip);
    function finish() {
      if (clip.done) return;
      clip.done = true;
      try { visual.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, easing: 'ease-in', fill: 'forwards' }); } catch (e) {}
      setTimeout(function () { self._clipEnd(clip); }, 320);
    }
    if (isVideo) {
      var sound = !!m.has_audio && this.audioOn && this.role === 'obs';
      el.autoplay = true; el.playsInline = true; el.loop = false;
      el.muted = !sound;
      if (sound && m.volume != null) el.volume = clamp(+m.volume, 0, 1);
      el.onended = finish;
      el.addEventListener('error', function () {
        self._clipError('video ' + (el.error ? (CLIP_ERR[el.error.code] || 'code ' + el.error.code) : 'unknown media error'), url);
        finish();
      });
      var start = m.start || 0, end = m.end;
      var seekIn = function () { try { if (start) el.currentTime = start; } catch (_) {} };
      if (el.readyState >= 1) seekIn(); else el.addEventListener('loadedmetadata', seekIn, { once: true });
      if (end != null) {
        var watch = function () { if (el.currentTime >= end) { el.removeEventListener('timeupdate', watch); try { el.pause(); } catch (_) {} finish(); } };
        el.addEventListener('timeupdate', watch);
      }
      var pp = el.play();
      if (pp && pp.catch) pp.catch(function () { el.muted = true; el.play().catch(function (e) { self._clipError('video play failed even muted: ' + e.message, url); finish(); }); });
    } else {
      el.onerror = function () { self._clipError('image failed to load', url); finish(); };
      setTimeout(finish, m.end != null ? Math.max(100, (m.end - (m.start || 0)) * 1000) : CLIP_STILL_MS);
    }
    var layer = this._clipLayer(at.layer !== 'back');
    layer.appendChild(visual);
    if (visual !== el && isVideo) layer.appendChild(el);
    try { visual.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: 'ease-out' }); } catch (e) {}
    return true;
  };

  Stage.prototype._clipEnd = function (clip) {
    var i = this.clips ? this.clips.indexOf(clip) : -1;
    if (i >= 0) this.clips.splice(i, 1);
    clip.done = true;
    if (clip.keyer) { try { clip.keyer.destroy(); } catch (e) {} }
    try { if (clip.el.pause) clip.el.pause(); } catch (e) {}
    [clip.visual, clip.el].forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); });
  };

  Stage.prototype.stopClips = function () {
    var list = (this.clips || []).slice(), self = this;
    list.forEach(function (c) { self._clipEnd(c); });
  };

  /* every frame, after the avatars: put each clip where the pin point it is locked to is now (page pixels) */
  Stage.prototype._clipsTick = function () {
    var list = this.clips;
    if (!list || !list.length) return;
    var s = this.scale || 1, page = { a: s, b: 0, c: 0, d: s, tx: this.root.position.x, ty: this.root.position.y };
    for (var i = 0; i < list.length; i++) {
      var c = list[i], av = this.avatars[c.avatar], at = c.at;
      if (!av || !av.model) { c.visual.style.visibility = 'hidden'; continue; }
      var f = av.attachFrame(at, at.follow_angle), pm = nodeMatrix(av.node);
      var m = mmul(page, mmul(pm, mmul(mT(f.x, f.y), mRot(f.rot))));
      var mirrored = pm.a * pm.d - pm.b * pm.c < 0;
      c.visual.style.transform = 'matrix(' + [m.a, m.b, m.c, m.d, m.tx, m.ty].map(function (v) { return v.toFixed(4); }).join(',') + ') rotate(' +
        (at.rotation || 0) + 'deg) scale(' + (at.scale || 1) + ') translate(' + (at.dx || 0) + '%,' + (at.dy || 0) + '%) scaleX(' +
        (mirrored && !at.mirror ? -1 : 1) + ') translate(-50%,-50%)';
      c.visual.style.visibility = 'visible';
    }
  };

  /* listen for the soundboard's clips locked to an avatar on this stage (the OBS source, and the tab's preview, silent) */
  Stage.prototype.listenClips = function () {
    if (this._clipWs || this._clipOff) return;
    var self = this, backoff = 800;
    function open() {
      var ws = self._clipWs = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/overlay?role=' + (self.role === 'obs' ? 'avatar' : 'preview'));
      ws.onopen = function () { backoff = 800; self._sendHave(); };
      ws.onmessage = function (ev) {
        var m; try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m.type === 'video' && m.attach) self.playClip(m);
        else if (m.type === 'stop') self.stopClips();
      };
      ws.onclose = function () {
        self._clipWs = null;
        if (!self._clipOff) setTimeout(open, backoff);
        backoff = Math.min(8000, backoff * 2);
      };
    }
    open();
  };

  /* tell the soundboard which avatars this stage draws, so it sends the clips locked to them here (and plays the rest itself) */
  Stage.prototype._sendHave = function () {
    var ws = this._clipWs;
    if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'avatars', names: Object.keys(this.avatars) }));
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
      var order = self.drawOrder || self.order;
      for (var i = order.length - 1; i >= 0; i--) {
        var av = self.avatars[order[i]];
        if (!av || !av.node.visible) continue;
        for (var j = av.items.length - 1; j >= 0; j--) {
          var it = av.items[j];
          if (!it.sprite || !it.node.visible || it.cfg.layer !== layer || it.cfg.locked) continue;       // (a locked item is not in the way: the click goes through to the model)
          var b = it.sprite.getBounds();
          if (gx >= b.x && gy >= b.y && gx <= b.x + b.width && gy <= b.y + b.height) return { av: av, it: it };
        }
      }
      return null;
    }
    return scan('front') || (this.pick(gx, gy) ? null : scan('back'));
  };

  /* the next click on the preview names the part of avatar `name` under it (`cb(partId)`, null if none): the Colors tab's "pick on the model" */
  Stage.prototype.pickPart = function (name, cb) {
    this._picking = { name: name, cb: cb };
    if (this.app) this.app.canvas.style.cursor = 'crosshair';
  };
  /* the same for a pin point: `cb(pin)` gets the spot clicked on the avatar's art (null if the click missed it) */
  Stage.prototype.pickPin = function (name, cb) {
    this._picking = { name: name, cb: cb, pin: true };
    if (this.app) this.app.canvas.style.cursor = 'crosshair';
  };
  Stage.prototype.cancelPick = function () {
    if (!this._picking) return;
    this._picking = null;
    if (this.app) this.app.canvas.style.cursor = 'default';
  };

  Stage.prototype.pick = function (gx, gy) {
    var order = this.drawOrder || this.order;
    for (var i = order.length - 1; i >= 0; i--) {
      var av = this.avatars[order[i]];
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
      if (self._picking) {                                          // "pick on the model" (the Colors tab): the click names the part under it
        var pk = self._picking, pav = self.avatars[pk.name];
        self.cancelPick();
        pk.cb(pav ? (pk.pin ? pav.pinAtGlobal(new PIXI.Point(p.gx, p.gy)) : pav.partAt(p.gx, p.gy)) : null);
        e.preventDefault();
        return;
      }
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
          drag = { kind: 'item', av: hitItem.av, it: it0, pinned: !!(it0.cfg.pin || it0.cfg.anchor), dx: it0.node.position.x - lp0.x, dy: it0.node.position.y - lp0.y };
          it0.cfg = Object.assign({}, it0.cfg, { pin: null, anchor: '' });            // follow the pointer while dragged
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
        if (av.mountParent) {                                          // hung from another avatar: the drag moves where it is glued
          var P = av.mountParent, at = av.cfg.attach, lg = P.node.toLocal(new PIXI.Point(p.gx, p.gy)), fr = P.attachFrame(at, at.follow_angle);
          var x0 = Math.round((fr.x / P.boxWidth() * 100 + 50) * 10) / 10, y0 = Math.round((fr.y / H * 100 + 50) * 10) / 10;       // (where it is glued now, as a box spot)
          drag = { kind: 'mount', av: av, P: P, pinned: fr.pinned, gx: lg.x - fr.x, gy: lg.y - fr.y, x: x0, y: y0 };      // (gx, gy: where the pointer holds it)
          av.cfg = Object.assign({}, av.cfg, { attach: Object.assign({}, at, { pin: null, anchor: '', x: x0, y: y0 }) });      // follow the pointer while dragged
        } else drag = { kind: 'move', av: av, sx: p.x, sy: p.y, start: transformOf(av) };
      }
      cv.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    cv.addEventListener('pointermove', function (e) {
      if (!drag && self._picking) { cv.style.cursor = 'crosshair'; return; }
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
      if (drag.kind === 'mount') {
        var lq = drag.P.node.toLocal(new PIXI.Point(p.gx, p.gy));
        drag.x = Math.round(((lq.x - drag.gx) / drag.P.boxWidth() * 100 + 50) * 10) / 10;
        drag.y = Math.round(((lq.y - drag.gy) / H * 100 + 50) * 10) / 10;
        drag.av.cfg.attach.x = drag.x; drag.av.cfg.attach.y = drag.y;
        drag.av._placeNode();
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
        if (d.pinned) { patch.pin = av.pinAt(d.it) || null; patch.anchor = ''; }       // a pinned item sticks where it is dropped
        self.emit('item', { avatar: av.name, id: d.it.cfg.id, patch: patch, final: true });
        return;
      }
      if (d.kind === 'mount') {
        var mp = { x: d.x, y: d.y };
        if (d.pinned) {                                                // glued to a part: it sticks to the part it is dropped on
          var fa = d.P.attachFrame(av.cfg.attach, false);
          mp.pin = d.P.pinAtGlobal(d.P.node.toGlobal(new PIXI.Point(fa.x, fa.y))) || null;
          mp.anchor = '';
        }
        self.emit('attach', { avatar: av.name, patch: mp, final: true });
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
      if (av.mountParent) t = { scale: t.scale };                    // (hung from another avatar: its own spot does not matter)
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

  function emptyLive() { return { params: {}, expressions: {}, emotion: null, face: null, look: null, light: null, transform: null, visible: null, outline: null, background: null }; }

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
    this.inBase = null;          // what the parameters the input layer added to were before it (see _hookLoad)
    this.inputs = Object.create(null);   // the input names the model's mappings take (set by _setInputs)
    this.partOv = {};            // part opacity overrides (the `parts` command)
    this.colOv = {};            // colours a bot holds for now (the `colors` command): 'all' / 'parts:id' / 'meshes:id' -> {kind, id, spec, until}
    this.presets = {};           // colour presets switched on (the `color_preset` command): name -> {look, until, order}
    this.colNow = {};              // what is drawn: 'a' / 'p:id' / 'm:id' -> {m: [r,g,b] multiply, s: [r,g,b] overlay, a: alpha}, easing toward colTo
    this.colTo = {};            // what each is heading for: the saved colours, the presets and the held ones merged (a later layer wins, field by field)
    this.colDirty = true;       // a layer changed: merge again
    this.colTau = 0.05;         // seconds the easing takes to cover most of the way (the fade of the last change / 3)
    this.colOn = false;
    this._colTbl = null;        // Live2D: the model's drawable -> part tables
    this._tinted = {};           // Live2D: drawable index -> true while its multiply / screen colour is ours
    this._tintedN = 0;
    this.hl = null;              // the preview's flash of a part / mesh (see `flash`)
    this._baseSrc = '';          // the saved colours (the config's `colors`) as last seen
    this._presetSeq = 0;
    this.exprs = {};             // active expressions
    this.emo = { name: 'neutral', face: {}, intensity: 1, until: 0, tau: 0.4 };
    this.apiFace = { face: {}, until: 0, tau: 0.25 };
    this.faceNow = { smile: 0, brows: 0, eyes: 1, mouth_open: 0, cheek: 0, mouth_x: 0, tilt: 0, wink_left: 0, wink_right: 0 };
    this.look = null;
    this.gaze = { ex: 0, ey: 0, hx: 0, hy: 0, tx: 0, ty: 0 };
    this.gestures = [];
    this.own = {};               // param index -> {w, to}: parameters the playing motion animates
    this.motionKey = '';
    this.motionNow = null;       // the motion the API started, until it ends: {key, name, id} (see _motionEnded)
    this.frame = null;
    this.acc = 0;
    this.msAcc = 0; this.msN = 0;
    this.items = [];
    this.itemSig = '';
    this.mountParent = null;     // the avatar this one is glued to (cfg.attach), when it is on this stage (see Stage._mounts)
    this.mountDepth = 0;         // how many avatars it hangs from, one under the other
    this.lightCfg = null;        // the look (see _lightTick): what is on, how far each is faded in
    this.lightW = 0;
    this.outCfg = null;
    this.outW = 0;
    this.bgCfg = null;           // the background that is on (one of cfg.backgrounds), and what is drawn for it
    this.bgW = 0;
    this.bgName = '';
    this.bg = null;
    this.fx = null;              // the avatar's filter, while any of it is on
    this.env = { fast: 0, slow: 0 };
    this._readColors(cfg, true);
  }

  Avatar.prototype.destroy = function () {
    this.stopSpeaking(true);
    this._clearItems();
    this._dropLook();
    this._dropBg();
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
    if (!this.outlineOverride) this.setOutline(cfg.outline, 0.4);
    this.setBackground(this.bgOverride ? this.bgLive : cfg.background, 0.4);   // (a held name, against the new list)
    this._readColors(cfg, false);
    this._syncItems();
    if (this.speech.cur && cfg.mouth) {                 // lipsync switched off, or back on, in the middle of a line
      if (cfg.mouth.lipsync === false) this.lip.attachAnalyser(null);
      else if (prev && prev.mouth && prev.mouth.lipsync === false && this.speech.cur.nodes) this.lip.attachAnalyser(this.speech.cur.nodes[0]);
    }
  };

  Avatar.prototype.unload = function () {
    this.loadSeq++;
    this.png = null;
    if (this.model) { try { this.model.destroy(); } catch (e) {} }
    this.model = null;
    this.modelId = '';
    this._colTbl = null; this._tinted = {}; this._tintedN = 0;
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
    this._colTbl = null; this._tinted = {}; this._tintedN = 0;          // a new model starts with its own colours
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
    // The engine updates a model (motions, physics, Cubism's own update: colours and opacities) right before it draws it,
    // so colours go on in between: after that update, before the draw
    var draw = im.draw;
    if (typeof draw === 'function') im.draw = function (gl) { self._applyColors(); return draw.call(this, gl); };
    im.on('afterMotionUpdate', function () { self._inputLayer(cm); });
    im.on('beforeModelUpdate', function () { self._finalLayer(cm); });
    this._hookLoad(cm);
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
    this._colTbl = null; this._tinted = {}; this._tintedN = 0;
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
    // a PNGtuber's expressions are its states and its expression pictures: the last one of each switched on is shown
    var on = Object.keys(this.exprs).filter(function (k) { return this.exprs[k].to > 0; }, this);
    var ons = on.filter(function (k) { return !rig.hasPicture(k); }).pop(), onp = on.filter(function (k) { return rig.hasPicture(k); }).pop();
    rig.setState(ons || null, ons ? this.exprs[ons].until : 0);
    if (onp) {
      rig.setPicture(onp, this.exprs[onp].until);
      if ((this.emo.exprs || []).indexOf(onp) >= 0) rig.emoPic = onp;
    }
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
    var seq = ++this.loadSeq, was = this.png;
    var keep = was ? { state: was.state, su: was.stateUntil, expr: was.expr, eu: was.exprUntil, emo: was.emoPic } : null;
    var self = this;
    this._loadPng(seq, this.modelId, this.meta, Object.assign({}, rig, { type: 'png', base: this.spec.base, url: this.spec.url }))
      .then(function () {
        if (!self.png || !keep || self.png === was) return;
        self.png.setState(keep.state, keep.su);
        if (keep.expr) { self.png.setPicture(keep.expr, keep.eu); self.png.emoPic = self.png.expr && self.png.expr === keep.emo ? keep.emo : ''; }
      });
  };

  /* the PNGtuber editor's voice and movement settings, live in the preview (no reload of the pictures) */
  Avatar.prototype.previewRigSettings = function (patch) {
    if (this.png) Object.assign(this.png.rig, patch);
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
    var partUp = null;
    try { partUp = cm.getPartParentPartIndices(); } catch (e) {}
    for (var i = 0; i < cm.getPartCount(); i++) {
      var pid = cm.getPartId(i).getString().s;
      parts.push({ id: pid, name: partNames[pid] || '', parent: partUp && partUp[i] >= 0 ? cm.getPartId(partUp[i]).getString().s : undefined });   // parts are folders: a sub-part names its parent
    }
    var drawableParts = [];
    for (i = 0; i < cm.getDrawableCount(); i++) {
      drawables.push(cm.getDrawableId(i).getString().s);
      var dp = cm.getDrawableParentPartIndex(i);
      drawableParts.push(dp >= 0 ? cm.getPartId(dp).getString().s : '');          // which part each art mesh is in
    }
    var hit = Object.keys(im.hitAreas || {});
    this.info = { parameters: params, parts: parts, drawables: drawables, drawable_parts: drawableParts, hit_areas: hit,
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
    this._colorTick(step, now);
    if (this.png) { var tp = performance.now(); this.png.update(step, this.frame, now); this.msAcc += performance.now() - tp; }
    else {
      this.model.update(step * 1000);                                      // (the model's own update runs when it is drawn: see _applyColors)
      if (this._wantsFreshPose()) this._poseNow();
    }
    for (var i = 0; i < this.items.length; i++) this.items[i].tick(step);
    if (this._pinMark) this._drawPinMark(now);
    this._lightTick(step, now);
    this.msAcc += performance.now() - t0;
    this.msN++;
  };

  /* The engine updates a model (motions, physics, Cubism's deformation) when it is drawn - after everything that is placed in
     the frame has been placed, so what is glued to a part of this model (an item, a clip, another avatar) would be put where
     the part was a frame ago and trail behind it. When something is glued to it the update is done here instead, at the start of
     the frame, and the draw finds nothing left to do: the same one update per frame, a little earlier. */
  Avatar.prototype._wantsFreshPose = function () {
    var i, k, av = this.stage.avatars, cl = this.stage.clips;
    if (this._pinMark) return true;
    for (i = 0; i < this.items.length; i++) { var c = this.items[i].cfg; if (c.pin || c.anchor) return true; }
    for (k in av) if (av[k].mountParent === this) return true;
    if (cl) for (i = 0; i < cl.length; i++) if (cl[i].avatar === this.name) return true;
    return false;
  };

  Avatar.prototype._poseNow = function () {
    var m = this.model, dt = m.deltaTime;
    if (!dt || !m.internalModel) return;
    m.deltaTime = 0;
    m.internalModel.update(dt, m.elapsedTime);
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

  /* Cubism saves the parameters right after the input layer below and puts them back at the end of every update, so whatever
     the input layer adds on top of a parameter - an expression, a value the API holds - would be the next frame's starting
     point: a parameter that nothing else drives (the toggle of a hat, a pair of wings) would never come back, and an Add
     expression would pile up to the parameter's maximum. So the input layer notes what each parameter it adds to was
     (`inBase`) and it is put back once the update is done. */
  Avatar.prototype._hookLoad = function (cm) {
    var self = this, load = cm.loadParameters;
    if (typeof load !== 'function' || cm.hexLoadHooked) return;
    cm.hexLoadHooked = true;
    cm.loadParameters = function () {
      var r = load.apply(cm, arguments), b = self.inBase;
      self.inBase = null;
      if (b) for (var k in b) cm.setParameterValueByIndex(+k, b[k]);
      return r;
    };
  };

  /* input layer: after the motions, before expressions are added and physics runs */
  Avatar.prototype._inputLayer = function (cm) {
    var F = this.frame;
    if (!F || !this.maps) return;
    var dt = F.dt, maps = this.maps, ov = this.ov, own = this.own, i, o, k;
    var base = this.inBase = {};
    this._trackMotion();
    var idleAnim = this.motionPri <= 1, talking = !!(F.lip && F.lip.level > 0.01) || !!this.speech.cur;
    for (k in own) {                                       // parameters a motion is animating
      var ow = own[k];
      // toward `to`, and rest when there: (an `else` that also took w == to ramped a fully owned parameter down every other frame,
      // so the tracker leaked into the animation at half the frame rate - the idle animation twitched)
      ow.w = ow.w < ow.to ? Math.min(ow.to, ow.w + dt / 0.3) : ow.w > ow.to ? Math.max(ow.to, ow.w - dt / 0.45) : ow.w;
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
        if (!(idx in base)) base[idx] = cur;
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
      if (layer === 'input' && this.inBase && !(idx in this.inBase)) this.inBase[idx] = cur;      // (the final layer is not saved: it goes by itself)
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
    if (this.motionNow && this.motionNow.key !== key) this._motionEnded('finished');      // (back to idle, or nothing)
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
    var c = this.tr.cur, at = this.cfg.attach, P = at && this.mountParent;
    if (P && P.model && P.node) {                         // glued to another avatar: ride its pin point (see Stage._mounts)
      var f = P.attachFrame(at, at.follow_angle), pm = nodeMatrix(P.node);
      var mirrored = pm.a * pm.d - pm.b * pm.c < 0;       // a flipped parent mirrors what hangs from it - its art is turned back unless `mirror`
      var cf = (this.tr.flip ? -1 : 1) * (mirrored && !at.mirror ? -1 : 1);
      var m = mmul(pm, mmul(mT(f.x, f.y), mRot(f.rot)));
      m = mmul(m, mmul(mRot(c.rotation * Math.PI / 180), mmul(mS(c.scale, c.scale), mmul(mT((at.dx || 0) / 100 * this.boxWidth(), (at.dy || 0) / 100 * H), mS(cf, 1)))));
      setNodeMatrix(this.node, m);
      return;
    }
    this.node.position.set(c.x / 100 * W, c.y / 100 * H);
    this.node.scale.set(c.scale * (this.tr.flip ? -1 : 1), c.scale);
    this.node.rotation = c.rotation * Math.PI / 180;
  };

  /* where the avatar stands on the stage now, as the settings that would put it there on its own (letting go of its parent
     keeps it where it is): {x, y, scale, rotation, flip} */
  Avatar.prototype.worldPlacement = function () {
    var m = nodeMatrix(this.node), s = Math.hypot(m.a, m.b) || 1, flip = m.a * m.d - m.b * m.c < 0, rot = Math.atan2(m.b, m.a) * 180 / Math.PI;
    if (flip) rot += 180;                                  // (the mirror is kept in `flip`, which mirrors before it rotates)
    if (rot > 180) rot -= 360;
    return { x: Math.round(m.tx / W * 1000) / 10, y: Math.round(m.ty / H * 1000) / 10, scale: Math.round(s * 1000) / 1000,
             rotation: Math.round(rot * 10) / 10, flip: flip };
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

  /* what is under a page point (renderer pixels): {mesh, part} - the topmost art mesh and its part - or, for a PNGtuber,
     {part: the topmost layer}; null if nothing */
  Avatar.prototype.partAt = function (gx, gy) {
    if (!this.model) return null;
    if (this.png) {
      var rp = this.png.draw.toLocal(new PIXI.Point(gx, gy)), layer = this.png.layerAt(rp.x, rp.y);
      return layer ? { mesh: null, part: layer.id } : null;
    }
    var im = this.model.internalModel, cm = im.coreModel;
    // a model's hit areas are invisible meshes laid over the art: not what is meant
    var lp = this.model.toLocal(new PIXI.Point(gx, gy)), hit = this.meshAt(lp.x, lp.y, this._hitSkip());
    if (!hit) return null;
    var p = cm.getDrawableParentPartIndex(hit.index);
    return { mesh: hit.id, part: p >= 0 ? this.Parts.ids[p] || null : null };
  };

  /* the topmost visible art mesh under a model-space point: {index, id, tri, bary} or null (`skip`: drawable indexes to look through) */
  Avatar.prototype.meshAt = function (x, y, skip) {
    if (this.png) return null;
    var im = this.model.internalModel, cm = im.coreModel, n = cm.getDrawableCount();
    var orders = cm.getDrawableRenderOrders(), list = [];
    for (var i = 0; i < n; i++) list.push(i);
    list.sort(function (a, b) { return orders[b] - orders[a]; });
    for (var li = 0; li < list.length; li++) {
      var d = list[li];
      if ((skip && skip[d]) || !cm.getDrawableDynamicFlagIsVisible(d) || cm.getDrawableOpacity(d) < 0.15) continue;
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

  /* a pin for the spot under a page point (renderer pixels): the art mesh triangle there (a PNGtuber: the layer), or null.
     A model's invisible hit areas are looked through - the art under them is what moves. */
  Avatar.prototype.pinAtGlobal = function (g) {
    if (!this.model) return null;
    if (this.png) {                                        // a PNGtuber: pinned to the layer under the point
      var rp = this.png.draw.toLocal(g), hitL = this.png.layerAt(rp.x, rp.y);
      return hitL ? { mesh: 'layer:' + hitL.id, tri: [0, 0, 0], bary: [hitL.lx, hitL.ly, 0], angle0: 0, follow_angle: true } : null;
    }
    var lp = this.model.toLocal(g), hit = this.meshAt(lp.x, lp.y, this._hitSkip());
    return hit ? { mesh: hit.id, tri: hit.tri, bary: hit.bary, angle0: hit.angle, follow_angle: true } : null;
  };

  /* a pin for an item at its current spot: the art mesh triangle under its centre, or null */
  Avatar.prototype.pinAt = function (it) {
    return it.node ? this.pinAtGlobal(it.node.getGlobalPosition()) : null;
  };

  /* one of the model's named pin points (any case): its pin, or null */
  Avatar.prototype.anchorPin = function (name) {
    var meta = this.stage.models[this.modelId], t = meta && meta.anchors;
    if (!name || !t) return null;
    if (t[name]) return t[name];
    var low = String(name).toLowerCase();
    for (var k in t) if (k.toLowerCase() === low) return t[k];
    return null;
  };

  /* where a pin sits right now, in this avatar's node space: {x, y, rot} (rot: how far the part under it has turned since the pin
     was made, in radians), or null when the part is not there (another model, not loaded) */
  Avatar.prototype.pinFrame = function (pin) {
    if (!pin) return null;
    if (this.png) {                                        // pinned to a PNGtuber layer: x, y in its own space
      if (!/^layer:/.test(pin.mesh)) return null;
      var gp = this.png.layerPoint(pin.mesh.slice(6), pin.bary[0], pin.bary[1]), ly = this.png.byId[pin.mesh.slice(6)];
      if (!gp || !ly) return null;
      var lp = this.node.toLocal(gp);
      return { x: lp.x, y: lp.y, rot: Math.atan2(ly.spriteM.b, ly.spriteM.a) };
    }
    if (!this.model) return null;
    try {
      var im = this.model.internalModel, d = im.getDrawableIndex(pin.mesh);
      if (d < 0) return null;
      var v = im.getDrawableVertices(d), t = pin.tri, b = pin.bary;
      var x = v[t[0] * 2] * b[0] + v[t[1] * 2] * b[1] + v[t[2] * 2] * b[2];
      var y = v[t[0] * 2 + 1] * b[0] + v[t[1] * 2 + 1] * b[1] + v[t[2] * 2 + 1] * b[2];
      var g = this.model.toGlobal(new PIXI.Point(x, y)), lq = this.node.toLocal(g);
      return { x: lq.x, y: lq.y, rot: Math.atan2(v[t[1] * 2 + 1] - v[t[0] * 2 + 1], v[t[1] * 2] - v[t[0] * 2]) - (pin.angle0 || 0) };
    } catch (e) { return null; }
  };

  /* the spot a thing glued to this avatar (an item, another avatar, a soundboard clip: `spec` has anchor, pin, x, y) is at, in
     this avatar's node space: {x, y, rot, pinned}. The model's pin point `spec.anchor`, else `spec.pin`, else the box spot x / y
     (% of the model's box: 0,0 its top left, 50,50 its centre). rot follows the part under the pin unless `follow` is false. */
  Avatar.prototype.attachFrame = function (spec, follow) {
    var pin = this.anchorPin(spec.anchor) || spec.pin, f = pin && this.pinFrame(pin);
    if (f) return { x: f.x, y: f.y, rot: follow !== false && pin.follow_angle !== false ? f.rot : 0, pinned: true };
    return { x: (spec.x - 50) / 100 * this.boxWidth(), y: (spec.y - 50) / 100 * H, rot: 0, pinned: false };
  };

  /* mark a pin (or the model's pin point called `pin` when it is a name) in the preview for a moment: a ring and a cross that
     ride the part, so the spot can be seen */
  Avatar.prototype.flashPin = function (pin, secs) {
    if (typeof pin === 'string') pin = this.anchorPin(pin);
    if (!pin || !this.model) return;
    if (!this._pinMark) { this._pinMark = new PIXI.Graphics(); this._pinMark.zIndex = 1e4; this.front.addChild(this._pinMark); }
    this._pinMarkPin = pin;
    this._pinMarkUntil = nowSec() + (secs || 2);
  };

  Avatar.prototype._drawPinMark = function (now) {
    var g = this._pinMark;
    g.clear();
    var left = this._pinMarkUntil - now;
    if (left <= 0) { this.front.removeChild(g); try { g.destroy(); } catch (e) {} this._pinMark = null; return; }
    var f = this.pinFrame(this._pinMarkPin);
    if (!f) return;
    var a = Math.min(1, left * 2) * (0.65 + 0.35 * Math.sin(now * 12));
    g.circle(f.x, f.y, 16).stroke({ width: 4, color: 0xff5fa2, alpha: a });
    g.moveTo(f.x - 24, f.y).lineTo(f.x + 24, f.y).moveTo(f.x, f.y - 24).lineTo(f.x, f.y + 24).stroke({ width: 3, color: 0xffffff, alpha: a });
  };

  /* the drawables a click should look through: a model's invisible hit areas (declared in its settings, or just named like one) */
  Avatar.prototype._hitSkip = function () {
    var im = this.model.internalModel, cm = im.coreModel, skip = {}, areas = im.hitAreas || {}, n;
    for (n in areas) {
      var di = areas[n].index >= 0 ? areas[n].index : (areas[n].id ? im.getDrawableIndex(areas[n].id) : -1);
      if (di >= 0) skip[di] = true;
    }
    for (var d = 0, nd = cm.getDrawableCount(); d < nd; d++) if (/^hit|hit_?area/i.test(cm.getDrawableId(d).getString().s)) skip[d] = true;
    return skip;
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
    for (k in live.colors || {}) {
      var held = live.colors[k], hl = {};
      if (held.kind === 'all') hl.all = held.spec; else { hl[held.kind] = {}; hl[held.kind][held.id] = held.spec; }
      this.command({ cmd: 'colors', colors: hl, until: held.until, restore: true });
    }
    for (k in live.color_presets || {}) {
      var pr = live.color_presets[k], ps = {}, pl = {}, po = {};
      ps[k] = 'on'; pl[k] = pr.rules; po[k] = pr.order;
      this.command({ cmd: 'color_preset', states: ps, rules: pl, orders: po, until: pr.until, restore: true });
    }
    for (k in live.expressions || {}) this.command({ cmd: 'expression', name: k, state: 'on', until: live.expressions[k] || null, fade: 0 });
    if (live.emotion) this.command(Object.assign({ cmd: 'emotion', fade: 0, previous: [] }, live.emotion));
    if (live.face) this.command(Object.assign({ cmd: 'face', fade: 0 }, live.face));
    if (live.look) this.command(Object.assign({ cmd: 'look' }, live.look));
    if (live.light) { this.lightOverride = true; this.setLight(live.light, 0); }
    if (live.outline) { this.outlineOverride = true; this.setOutline(live.outline, 0); }
    if (live.background) { this.bgOverride = true; this.bgLive = live.background.name; this.setBackground(this.bgLive, 0); }
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
      case 'colors': {                                      // recolour for now: {colors: {all, parts, meshes}}; a field set to null goes back (saved ones arrive as config)
        var self2 = this;
        eachTarget(c.colors || {}, function (kind, id, spec) {
          var key = colorKey(kind, id);
          if (c.save) { delete self2.colOv[key]; return; }
          var held = self2.colOv[key] || { kind: kind, id: id, spec: {}, until: 0 };
          for (var f in spec) { if (spec[f] == null) delete held.spec[f]; else held.spec[f] = spec[f]; }
          held.until = c.until || 0;
          if (Object.keys(held.spec).length) self2.colOv[key] = held; else delete self2.colOv[key];
        });
        this._colored(c);
        break;
      }
      case 'release_colors': {
        if (c.everything) this.colOv = {};
        else {
          if (c.all) delete this.colOv.all;
          (c.parts || []).forEach(function (id) { delete this.colOv[colorKey('parts', id)]; }, this);
          (c.meshes || []).forEach(function (id) { delete this.colOv[colorKey('meshes', id)]; }, this);
        }
        this._colored(c);
        break;
      }
      case 'color_preset': {                                // a colour preset switched on / off (the rules come with the command)
        var pst = c.states || {};
        for (k in pst) {
          if (pst[k] === 'on' && c.rules && c.rules[k]) this.presets[k] = { look: c.rules[k], until: c.until || 0, order: (c.orders && c.orders[k]) || ++this._presetSeq };
          else if (pst[k] !== 'on') delete this.presets[k];
        }
        this._colored(c);
        break;
      }
      case 'clear_color_presets': this.presets = {}; this._colored(c); break;
      case 'release': {
        var ids = c.ids || Object.keys(this.ov);
        for (var i = 0; i < ids.length; i++) { var o = this.ov[ids[i]]; if (o) { o.to = 0; o.rate = c.fade == null ? 0.3 : c.fade; o.until = 0; } }
        break;
      }
      case 'expression':                                   // one, or several in the same frame
        if (this.png) {                    // a PNGtuber shows one state at a time, and one expression picture at a time over it
          var sts = c.states || (function () { var o = {}; o[c.name] = c.state === 'off' ? 'off' : 'on'; return o; })(), pr = this.png;
          for (k in sts) {
            if (pr.hasPicture(k)) {
              if (sts[k] === 'on') pr.setPicture(k, c.until);
              else if (pr.expr === k) pr.setPicture(null);
            } else if (sts[k] === 'on') { if (pr.isState(k)) pr.setState(k, c.until); }      // (a name it hasn't got changes nothing)
            else if (pr.state === k) pr.setState(null);
          }
          break;
        }
        if (c.states) { for (k in c.states) this._expression(k, c.states[k] === 'on', c.fade, c.until); }
        else this._expression(c.name, c.state !== 'off', c.fade, c.until);
        // switched by itself, an expression is no longer the emotion's: the emotion running out does not touch it
        var mine = c.states ? Object.keys(c.states) : [c.name];
        if (this.emo.exprs) this.emo.exprs = this.emo.exprs.filter(function (n) { return mine.indexOf(n) < 0; });
        break;
      case 'clear_expressions': if (this.png) { this.png.setState(null); this.png.setPicture(null); } for (k in this.exprs) { this.exprs[k].to = 0; this.exprs[k].fadeOut = c.fade || this.exprs[k].fadeOut; } this.emo.exprs = []; break;
      case 'motion': this._motion(c); break;
      case 'stop_motion': if (this.model && !this.png) { this.model.internalModel.motionManager.stopAllMotions(); this._motionEnded('stopped'); } break;
      case 'emotion': {
        var self = this;
        if (this.png) {                                   // its state (or the default), and an expression picture if it names one
          var pr2 = this.png, wants = c.name === 'neutral' ? [] : ((c.expressions || []).length ? c.expressions : [c.name]);
          var pic = wants.filter(function (n) { return pr2.hasPicture(n); })[0];
          if (pr2.emoPic && pr2.expr === pr2.emoPic) pr2.setPicture(null);           // the last emotion's picture goes
          pr2.emoPic = '';
          pr2.setState(wants.filter(function (n) { return !pr2.hasPicture(n); })[0] || null);
          if (pic) { pr2.setPicture(pic); pr2.emoPic = pic; }
        }
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
      case 'outline': this.outlineOverride = !c.save && !c.reset; this.setOutline(c.outline, c.fade == null ? 0.4 : c.fade); break;
      case 'background':
        this.bgOverride = !c.save && !c.reset; this.bgLive = c.background || '';
        this.setBackground(this.bgLive, c.fade == null ? 0.4 : c.fade);
        break;
      case 'item_add': case 'item_remove': case 'item_update': case 'items_clear': case 'attach': case 'detach': case 'lipsync': break;   // arrives as config
      case 'reload': if (this.cfg.model) this.load(this.cfg.model); break;
    }
  };

  Avatar.prototype._emotionOff = function () {
    var self = this;
    if (this.png) {
      this.png.setState(null);
      if (this.png.emoPic && this.png.expr === this.png.emoPic) this.png.setPicture(null);
      this.png.emoPic = '';
    }
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
    // FORCE takes over from the motion playing; NORMAL is skipped while another one plays. No jump either way: the parameters
    // are saved after the motions each frame, so the new motion fades in from the pose the model is in
    var pri = c.priority === 'normal' ? 2 : 3, id = c.id, model = this.model;
    model.motion(group, index == null ? undefined : index, pri, {
      loop: !!c.loop,       // always say: left out, the engine takes the file's Loop flag - on in most exported motions, which then never end
      onError: function (e) { self.stage.emit('event', { event: 'error', avatar: self.name, message: 'motion: ' + (e && e.message || e) }); }
    }).then(function (ok) {
      if (!ok || self.model !== model) return;
      var st = model.internalModel.motionManager.state || {};
      self._motionEnded('replaced');
      self.motionNow = { key: st.currentGroup + '#' + st.currentIndex, name: c.name || group, id: id };
      self.stage.emit('event', { event: 'motion_start', avatar: self.name, name: c.name || group, id: id });
    });
  };

  /* the motion started through the API is over: `motion_end` (the engine's own finish callback only runs for a motion with a sound) */
  Avatar.prototype._motionEnded = function (reason) {
    var m = this.motionNow;
    if (!m) return;
    this.motionNow = null;
    this.stage.emit('event', { event: 'motion_end', avatar: this.name, name: m.name, id: m.id, reason: reason });
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

  /* a picture of the item library as something to draw: a sprite, an animated GIF, a frame sequence, a Live2D model */
  async function loadDisplay(meta, fps) {
    var disp = null;
    if (meta.kind === 'image') {
      disp = new PIXI.Sprite(await PIXI.Assets.load(meta.url));
    } else if (meta.kind === 'gif') {
      var res = await PIXI.Assets.load(meta.url);                  // @pixi/gif answers .gif with an AnimatedGIF
      disp = res instanceof PIXI.Texture ? new PIXI.Sprite(res) : (res.clone ? res.clone() : res);
      if (disp.play) disp.play();
    } else if (meta.kind === 'sequence') {
      var tex = await Promise.all(meta.frames.map(function (u) { return PIXI.Assets.load(u); }));
      disp = new PIXI.AnimatedSprite(tex);
      disp.animationSpeed = (fps || meta.fps || 12) / 60;
      disp.play();
    } else if (meta.kind === 'live2d') {
      disp = await L.Live2DModel.from(meta.url, { autoUpdate: false, autoFocus: false, autoHitTest: false });
    }
    return disp;
  }

  Item.prototype._load = async function () {
    var meta = this.meta, self = this, disp = null;
    try {
      disp = await loadDisplay(meta, this.cfg.fps);
      if (meta.kind === 'live2d') this.l2d = disp;
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

  /* at the model's pin point (`anchor`), else the raw pin, else - not pinned - x / y in % of the model's own box
     (0,0 its top left, 50,50 its centre); an anchor the model lacks (another model) falls back the same way */
  Item.prototype._place = function () {
    var cfg = this.cfg, f = this.av.attachFrame(cfg);
    this.node.position.set(f.x, f.y);
    this.node.rotation = (cfg.rotation || 0) * Math.PI / 180 + f.rot;
  };

  Item.prototype.tick = function (dt) {
    if (!this.ready) return;
    if (this.l2d) this.l2d.update(dt * 1000);
    if (this.cfg.pin || this.cfg.anchor) this._place();
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
  function mRot(r) { var c = Math.cos(r), s = Math.sin(r); return { a: c, b: s, c: -s, d: c, tx: 0, ty: 0 }; }
  function mS(x, y) { return { a: x, b: 0, c: 0, d: y, tx: 0, ty: 0 }; }
  var M_ID = mT(0, 0);
  /* a display object's own transform (position, rotation, scale; no pivot or skew) as a matrix, and the other way round
     (a matrix of a rotation, an even scale and maybe a mirror: the scale is signed so the mirror is kept) */
  function nodeMatrix(n) { return mmul(mT(n.position.x, n.position.y), mmul(mRot(n.rotation), mS(n.scale.x, n.scale.y))); }
  function setNodeMatrix(n, m) {
    var sx = Math.hypot(m.a, m.b) || 1e-6;
    n.position.set(m.tx, m.ty);
    n.rotation = Math.atan2(m.b, m.a);
    n.scale.set(sx, (m.a * m.d - m.b * m.c) / sx);
  }
  function mApply(m, x, y) { return { x: m.a * x + m.c * y + m.tx, y: m.b * x + m.d * y + m.ty }; }
  function mInv(m) {
    var det = m.a * m.d - m.b * m.c || 1e-9;
    return { a: m.d / det, b: -m.b / det, c: -m.c / det, d: m.a / det, tx: (m.c * m.ty - m.d * m.tx) / det, ty: (m.b * m.tx - m.a * m.ty) / det };
  }

  /* the pictures of one simple state (or of one expression picture) as layers, with the show-when rules of each picture */
  function roleLayers(out, key, s, extra) {
    var talks = !!(s.talk || s.talk_blink || s.half || s.A || s.I || s.U || s.E || s.O);
    function add(role, talk, blink, vowel) {
      if (!s[role]) return;
      out.push(Object.assign({ id: key + ':' + role, name: key + ' ' + role, image: s[role], parent: null, x: 0, y: 0, ox: 0, oy: 0, z: 0,
                 talk: talk, blink: blink, vowel: vowel || '', states: [], wobble: [0, 0, 0, 0], drag: 0, rot_drag: 0,
                 rot_min: -180, rot_max: 180, stretch: 0, ignore_bounce: false, frames: 1, fps: 0, clip: false, toggle: '' }, extra));
    }
    add('idle', talks ? 1 : 0, s.blink ? 1 : 0);
    add('blink', talks ? 1 : 0, 2);
    add('talk', 2, s.talk_blink ? 1 : 0);
    add('talk_blink', 2, 2);
    add('half', 3, 0);
    ['A', 'I', 'U', 'E', 'O'].forEach(function (v) { add(v, 2, 0, v); });
  }
  /* a simple rig (pictures per state) as layers */
  function simpleLayers(rig) {
    var out = [];
    Object.keys(rig.states || {}).forEach(function (st) { roleLayers(out, st, rig.states[st], { states: [st] }); });
    return out;
  }
  /* the expression pictures (either style) as layers: each stands in for the whole avatar while it is on */
  function pictureLayers(rig) {
    var out = [];
    Object.keys(rig.expressions || {}).forEach(function (n) { roleLayers(out, 'expr:' + n, rig.expressions[n] || {}, { expr: n }); });
    return out;
  }

  /* A PNGtuber's mouth, open or shut, from the voice level. It follows the syllables: it opens when the voice
     rises and shuts in the dips between words and sounds, instead of staying open for as long as anything is
     said. A dip is measured against the recent peak of the voice (which falls slowly), so quiet and loud voices
     behave alike; two thresholds (a gap between "opens" and "shuts") and a shortest time in each state keep it
     from flickering. `snap` 0..1 is how deep a dip has to be to shut it: low = lazy, high = every dip. */
  var GATE_PEAK = 0.45;          // seconds for the remembered peak to fall by a factor e
  function MouthGate() { this.open = false; this.t = 1; this.ref = 0; }
  MouthGate.prototype.update = function (level, dt, o) {
    var snap = clamp(o.snap == null ? 0.6 : o.snap, 0, 1);
    var closeAt = 0.5 + 0.4 * snap, openAt = Math.min(0.97, closeAt + 0.1);       // fractions of the recent peak
    this.t += dt;
    this.ref = Math.max(level, this.ref * Math.exp(-dt / GATE_PEAK));
    var hi = Math.max(o.threshold, this.ref * openAt), lo = Math.max(o.threshold * 0.8, this.ref * closeAt);
    if (this.open) { if (level < lo && this.t >= o.minOpen) { this.open = false; this.t = 0; } }
    else if (level > hi && this.t >= o.minClosed) { this.open = true; this.t = 0; }
    return this.open;
  };
  MouthGate.prototype.shut = function () { if (this.open) { this.open = false; this.t = 0; } };
  HA.MouthGate = MouthGate;

  var SPEAK_HOLD = 0.35;         // seconds of quiet that end "speaking" (the pauses between words don't)

  function PngRig(av, rig) {
    this.av = av;
    this.rig = rig;
    this.root = new PIXI.Container();        // the avatar's holder holds this: bounce, head motion, breathing
    this.root.label = 'pngtuber';
    this.draw = new PIXI.Container();
    this.draw.sortableChildren = true;
    this.root.addChild(this.draw);
    var src = (rig.style === 'simple' ? simpleLayers(rig) : (rig.layers || [])).concat(pictureLayers(rig));
    this.layers = src.map(function (l) { return Object.assign({}, l, { s: { dx: null, dy: null, rot: 0, sx: 1, sy: 1, ft: 0 } }); });
    this.byId = {};
    var self = this;
    this.layers.forEach(function (l) { self.byId[l.id] = l; });
    this.parts = { ids: this.layers.map(function (l) { return l.id; }), index: {} };
    this.layers.forEach(function (l, i) { self.parts.index[l.id] = i; });
    this.state = rig.default_state; this.stateUntil = 0;
    // the expression picture shown over the state ('' = none), until when, and whether an emotion put it there
    this.pics = {}; this.expr = ''; this.exprUntil = 0; this.emoPic = '';
    this.layers.forEach(function (l) { if (l.expr) self.pics[l.expr] = true; });
    this.talking = false; this.soft = false; this.blinking = false; this.vowel = ''; this.level = 0;
    this.speaking = false; this.speakHold = 0; this.gate = new MouthGate();     // `talking` = the mouth is open
    this.hold = 0; this.by = 0; this.vy = 0; this.t = 0;
    // per state: does it have a half-open mouth / vowel mouths (they take over from the plain one)
    this.stateInfo = {};
    this.layers.forEach(function (l) {
      (l.expr ? ['@' + l.expr] : l.states && l.states.length ? l.states : ['*']).forEach(function (st) {
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
    // an expression picture is drawn where the avatar is: centred on it (a simple rig's pictures all are, on 0, 0)
    if (this.rig.style !== 'simple') {
      var cx = this.box.x + this.box.w / 2, cy = this.box.y + this.box.h / 2;
      this.layers.forEach(function (l) { if (l.expr) { l.x = cx; l.y = cy; } });
    }
    if (!this.layers.some(function (l) { return l.sprite; })) throw new Error('none of its pictures could be loaded');
  };

  PngRig.prototype.isState = function (name) {
    return !!name && !!(this.rig.style === 'simple' ? (this.rig.states || {})[name] : (this.rig.states || []).indexOf(name) >= 0);
  };
  PngRig.prototype.setState = function (name, until) {
    var ok = this.isState(name);
    this.state = ok ? name : this.rig.default_state;
    this.stateUntil = ok && until || 0;
  };

  /* an expression picture over the state (null: none) - one at a time */
  PngRig.prototype.hasPicture = function (name) { return !!name && !!this.pics[name]; };
  PngRig.prototype.setPicture = function (name, until) {
    this.expr = this.hasPicture(name) ? name : '';
    this.exprUntil = this.expr && until || 0;
    this.emoPic = '';
  };

  PngRig.prototype.hop = function (amount) { if (this.by > -16) this.vy = -this.rig.bounce * (amount || 1); };

  /* is this layer shown right now */
  PngRig.prototype._shown = function (l) {
    if (this.expr) { if (l.expr !== this.expr) return false; }          // an expression picture: only its own pictures
    else if (l.expr) return false;
    else if (l.states && l.states.length && l.states.indexOf(this.state) < 0) return false;
    var si = this.stateInfo[this.expr ? '@' + this.expr : this.state] || this.stateInfo['*'] || { half: false, vowels: {} }, t = this.talking;
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
      l.sprite.alpha = (o && o.w > 0 ? lerp(1, o.value, o.w) : 1) * this.av.pngAlpha(l.id);
      l.sprite.tint = this.av.pngTint(l.id);
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
    // a state or an expression picture switched on for a while (the call's "for")
    if (this.exprUntil && now >= this.exprUntil) this.setPicture(null);
    if (this.stateUntil && now >= this.stateUntil) this.setState(null);
    // the voice (or the bot's MouthOpen / mouth_open)
    // (the unsmoothed level when there is one: the dips between words are what the mouth follows)
    var level = Math.max(lip.raw != null ? lip.raw : (lip.level || 0), av.inputOv('MouthOpen', F ? F.MouthOpen : 0));
    this.level = level;
    var thr = r.threshold == null ? 0.12 : r.threshold;
    var wasOpen = this.talking, wasSpeaking = this.speaking, started;
    if (r.mouth === 'hold') {
      // the old way: open at any sound and stay open for `hold` seconds after it
      if (level > thr) this.hold = r.hold; else this.hold = Math.max(0, this.hold - dt);
      this.talking = this.hold > 0;
      this.speaking = this.talking;
      started = this.talking && !wasOpen;
    } else {
      // speaking = there is a voice (kept through the pauses between words); the mouth follows its syllables
      if (level > thr) this.speakHold = SPEAK_HOLD; else this.speakHold = Math.max(0, this.speakHold - dt);
      this.speaking = this.speakHold > 0;
      this.talking = this.gate.update(level, dt, { threshold: thr, snap: r.snap, minOpen: 0.05, minClosed: 0.045 });
      if (!this.speaking && this.talking) { this.gate.shut(); this.talking = false; }
      started = this.speaking && !wasSpeaking;
    }
    var opened = this.talking && !wasOpen;
    this.soft = this.talking && level < thr * 2.4;
    var best = '', bv = 0.3;
    if (this.talking && lip.level > 0.02) ['A', 'I', 'U', 'E', 'O'].forEach(function (v) { if ((lip[v] || 0) > bv) { bv = lip[v]; best = v; } });
    this.vowel = best;
    var eyes = Math.min(av.inputOv('EyeOpenLeft', F ? F.eyeL : 1), av.inputOv('EyeOpenRight', F ? F.eyeR : 1));
    this.blinking = eyes < 0.4;
    // the bounce when speech starts (PNGTuber Plus: up at `bounce` px/s, falling at `gravity` px/s2)
    var sm = ((av.cfg.idle && av.cfg.idle.speech_motion) == null ? 50 : av.cfg.idle.speech_motion) / 50;
    if (started && this.by > -16 && r.bounce > 0) this.vy = -r.bounce * sm;
    else if (opened && r.mouth !== 'hold' && r.beat > 0 && this.by > -5.5) {
      // a small hop with each word as the voice comes in (louder voice, bigger hop). It only starts from the ground or
      // the last of a fall, so hops land between words instead of stacking up into a float
      this.vy = Math.min(this.vy, -r.beat * sm * (0.55 + 0.45 * clamp(this.gate.ref, 0, 1)));
    }
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

  /* -------------------------------------------- colours
   *
   * VTube Studio's "customize multiply / screen colour for art meshes", and then some. A look names targets - the
   * whole model (`all`), parts (`parts`: Live2D parts are folders of art meshes and sub-parts; a PNGtuber's layers)
   * and single art meshes (`meshes`) - and gives each any of `multiply` (a colour that darkens / tints: the art
   * is multiplied by it), `overlay` (a colour that lightens: Cubism's screen colour, the art plus the colour) and
   * `alpha` (0 invisible .. 1). Targets stack: a mesh's multiply is its own x its parts' x the whole model's,
   * overlays combine as screens, alphas multiply. Over a mesh's own multiply / screen colour (Cubism 4.2+/5 models
   * may have them) and over its opacity as the model has just made it.
   *
   * Looks come in layers, a later one winning field by field: the avatar's saved colours (config `colors`), the
   * colour presets switched on (the model's, in the order they went on) and what a bot holds for now (`colors`).
   * The merged result eases to its values, so every change fades.
   *
   * Live2D: the multiply / screen colour of each art mesh is the engine's per-drawable override, and the alpha is
   * written into the model's drawable opacities - both after the model's update, before it is drawn. PNGtuber:
   * a layer's sprite tint and alpha (no overlay colour there); a layer's parent is its motion rig, not a group,
   * so a layer's colour stays on that layer. */

  var NEUTRAL7 = [1, 1, 1, 0, 0, 0, 1];                       // multiply rgb, overlay rgb, alpha: nothing changed
  function neutral7(t) { return t[0] > 0.998 && t[1] > 0.998 && t[2] > 0.998 && t[3] < 0.002 && t[4] < 0.002 && t[5] < 0.002 && t[6] > 0.998; }
  function mix7(x, y) {
    return [x[0] * y[0], x[1] * y[1], x[2] * y[2], 1 - (1 - x[3]) * (1 - y[3]), 1 - (1 - x[4]) * (1 - y[4]), 1 - (1 - x[5]) * (1 - y[5]), x[6] * y[6]];
  }
  function colorKey(kind, id) { return kind === 'all' ? 'all' : kind + ':' + id; }
  function eachTarget(look, fn) {                             // every target a look names: (kind, id, spec)
    if (look.all) fn('all', '', look.all);
    var id;
    for (id in look.parts || {}) fn('parts', id, look.parts[id]);
    for (id in look.meshes || {}) fn('meshes', id, look.meshes[id]);
  }

  /* the saved colours (config `colors`): `snap` - a new avatar starts in them, it does not fade in */
  Avatar.prototype._readColors = function (cfg, snap) {
    var src = JSON.stringify((cfg && cfg.colors) || {});
    if (src === this._baseSrc && !snap) return;
    this._baseSrc = src;
    this.colDirty = true;
    if (snap) {
      this._resolveColors();
      for (var k in this.colTo) this.colNow[k] = this._toCurrent(this.colTo[k]);
    } else this.colTau = 0.05;                              // a colour picker being dragged: follow it closely
  };

  /* a command changed a layer: merge again, and fade over its `fade` (a restored one is there at once) */
  Avatar.prototype._colored = function (c) {
    this.colDirty = true;
    this.colTau = c.restore ? 0.0001 : Math.max(0.0001, (c.fade == null ? 0.3 : c.fade) / 3);
  };

  Avatar.prototype._toCurrent = function (to) {
    return { m: to.m ? to.m.slice() : [1, 1, 1], s: to.s ? to.s.slice() : [0, 0, 0], a: to.a == null ? 1 : to.a };
  };

  /* merge the layers: saved colours, then the presets (in the order they went on), then what a bot holds */
  Avatar.prototype._resolveColors = function () {
    var to = {};
    function put(key, spec) {
      var o = to[key] || (to[key] = {});
      if (spec.multiply != null) o.m = hexRgb(spec.multiply);
      if (spec.overlay != null) o.s = hexRgb(spec.overlay);
      if (spec.alpha != null) o.a = clamp(+spec.alpha, 0, 1);
    }
    function layer(look) { if (look) eachTarget(look, function (kind, id, spec) { put(kind === 'all' ? 'a' : (kind === 'parts' ? 'p:' : 'm:') + id, spec); }); }
    layer((this.cfg && this.cfg.colors) || {});
    var self = this;
    Object.keys(this.presets).map(function (n) { return self.presets[n]; }).sort(function (x, y) { return x.order - y.order; })
      .forEach(function (p) { layer(p.look); });
    for (var k in this.colOv) {
      var h = this.colOv[k];
      put(h.kind === 'all' ? 'a' : (h.kind === 'parts' ? 'p:' : 'm:') + h.id, h.spec);
    }
    this.colTo = to;
    this.colDirty = false;
  };

  Avatar.prototype._colorTick = function (dt, now) {
    var k;
    for (k in this.colOv) if (this.colOv[k].until && now >= this.colOv[k].until) { delete this.colOv[k]; this.colDirty = true; }
    for (k in this.presets) if (this.presets[k].until && now >= this.presets[k].until) { delete this.presets[k]; this.colDirty = true; }
    if (this.colDirty) this._resolveColors();
    var busy = false;
    for (k in this.colTo) { busy = true; break; }
    if (!busy) for (k in this.colNow) { busy = true; break; }
    if (!busy) { this.colOn = false; return; }
    var keys = {}, any = false, i;
    for (k in this.colTo) keys[k] = 1;
    for (k in this.colNow) keys[k] = 1;
    for (k in keys) {
      var cur = this.colNow[k] || (this.colNow[k] = { m: [1, 1, 1], s: [0, 0, 0], a: 1 }), to = this._toCurrent(this.colTo[k] || {});
      for (i = 0; i < 3; i++) {
        cur.m[i] = approach(cur.m[i], to.m[i], dt, this.colTau); if (Math.abs(cur.m[i] - to.m[i]) < 0.002) cur.m[i] = to.m[i];
        cur.s[i] = approach(cur.s[i], to.s[i], dt, this.colTau); if (Math.abs(cur.s[i] - to.s[i]) < 0.002) cur.s[i] = to.s[i];
      }
      cur.a = approach(cur.a, to.a, dt, this.colTau); if (Math.abs(cur.a - to.a) < 0.002) cur.a = to.a;
      if (neutral7(flat7(cur)) && neutral7(flat7(to))) delete this.colNow[k]; else any = true;
    }
    this.colOn = any;
  };
  function flat7(c) { return [c.m[0], c.m[1], c.m[2], c.s[0], c.s[1], c.s[2], c.a]; }

  /* Live2D: each art mesh's colours and opacity = what the model made of them, then the look of its part (and the parts around it), `all` and itself.
     Called by the model's draw (see load), which is after its update - and may come more than once for one update. */
  Avatar.prototype._applyColors = function () {
    if (this.png || !this.model || (!this.colOn && !this._tintedN && !this.hl)) return;
    var cm = this.model.internalModel && this.model.internalModel.coreModel;
    if (!cm || !cm.setMultiplyColorByRGBA || !cm.setScreenColorByRGBA) return;       // a runtime without Cubism 5's colour overrides: no colours
    var T = this._colTbl;
    if (!T) {
      var nd = cm.getDrawableCount(), dpart = new Int32Array(nd), dids = [], parent = null;
      for (var di = 0; di < nd; di++) { dpart[di] = cm.getDrawableParentPartIndex(di); dids.push(cm.getDrawableId(di).getString().s); }
      try { parent = cm.getPartParentPartIndices(); } catch (e) {}
      var dr = cm._model && cm._model.drawables;
      // `nat` / `wrote`: the opacity the model had made of each mesh, and what we put there (see below)
      T = this._colTbl = { nd: nd, dpart: dpart, dids: dids, parent: parent, eff: [], gen: [], stamp: 0, ops: dr && dr.opacities, nat: {}, wrote: {} };
    }
    var self = this, ids = this.Parts.ids, look = this.colNow, stamp = ++T.stamp;
    var whole = look.a ? flat7(look.a) : NEUTRAL7;
    function effective(p, depth) {                                // a part's look: its own x its parents' (memoised for this frame)
      if (p < 0 || p >= ids.length || depth > 64) return whole;
      if (T.gen[p] === stamp) return T.eff[p];
      var own = look['p:' + ids[p]], up = effective(T.parent ? T.parent[p] : -1, depth + 1);
      var r = own ? mix7(up, flat7(own)) : up;
      T.gen[p] = stamp; T.eff[p] = r;
      return r;
    }
    // a flash (the preview's, see `flash`): which meshes, and how bright it is right now
    var hl = this.hl, hlAmp = 0, hlSet = null;
    if (hl) {
      var hu = (nowSec() - hl.t0) / hl.dur;
      if (hu >= 1) this.hl = hl = null;
      else {
        hlAmp = (1 - hu) * (0.5 + 0.5 * Math.sin((nowSec() - hl.t0) * 15));
        if (!hl.set) {                                            // the drawables of a mesh, of a part and what is inside it, or of everything
          hl.set = {};
          for (var hd = 0; hd < T.nd; hd++) {
            if (hl.kind === 'all' || (hl.kind === 'meshes' && T.dids[hd] === hl.id)) { hl.set[hd] = 1; continue; }
            for (var hp = T.dpart[hd], hn = 0; hl.kind === 'parts' && hp >= 0 && hp < ids.length && hn < 64; hn++) {
              if (ids[hp] === hl.id) { hl.set[hd] = 1; break; }
              hp = T.parent ? T.parent[hp] : -1;
            }
          }
        }
        hlSet = hl.set;
      }
    }
    // The model does not rewrite an opacity on an update where nothing changed, so `opacity *= alpha` every frame would
    // keep shrinking it. What is in the array is ours if it is still what we wrote - then the model's own value is the one
    // we kept - and the model's if not.
    function modelOpacity(d) {
      var v = T.ops[d];
      return T.wrote[d] !== undefined && T.wrote[d] === v ? T.nat[d] : v;
    }
    for (var d = 0; d < T.nd; d++) {
      var t = effective(T.dpart[d], 0), me = look['m:' + T.dids[d]];
      if (me) t = mix7(t, flat7(me));
      if (hlSet && hlSet[d]) {                                    // flashing: lit up pink, and shown even when it is hidden
        t = mix7(t, [1, 1, 1, hlAmp, hlAmp * 0.25, hlAmp * 0.6, 1]);
        if (t[6] < hlAmp) t[6] = hlAmp;
      }
      if (T.ops && t[6] >= 0.998 && T.wrote[d] !== undefined) {         // no alpha now: hand the opacity back
        if (T.ops[d] === T.wrote[d]) T.ops[d] = T.nat[d];
        delete T.wrote[d]; delete T.nat[d];
      }
      if (neutral7(t)) {
        if (this._tinted[d]) {
          cm.setOverrideFlagForDrawableMultiplyColors(d, false); cm.setOverrideFlagForDrawableScreenColors(d, false);
          delete this._tinted[d]; this._tintedN--;
        }
        continue;
      }
      var nm = cm.getDrawableMultiplyColor(d), ns = cm.getDrawableScreenColor(d);
      cm.setMultiplyColorByRGBA(d, nm.r * t[0], nm.g * t[1], nm.b * t[2], nm.a);
      cm.setScreenColorByRGBA(d, 1 - (1 - ns.r) * (1 - t[3]), 1 - (1 - ns.g) * (1 - t[4]), 1 - (1 - ns.b) * (1 - t[5]), ns.a);
      if (!this._tinted[d]) {
        cm.setOverrideFlagForDrawableMultiplyColors(d, true); cm.setOverrideFlagForDrawableScreenColors(d, true);
        this._tinted[d] = true; this._tintedN++;
      }
      if (t[6] < 0.998 && T.ops) {
        var nat = modelOpacity(d);
        T.ops[d] = nat * t[6];
        T.nat[d] = nat; T.wrote[d] = T.ops[d];
      }
    }
  };

  /* The preview only: make a part, an art mesh or everything flash for a moment, so a row of the Colors tab can be found
     on the model (a hidden mesh shows through while it flashes). Never on stream: only the tab's preview takes this. */
  Avatar.prototype.flash = function (kind, id, secs) {
    if (this.stage.role !== 'preview' || this.png) return;
    this.hl = { kind: kind, id: id, t0: nowSec(), dur: secs || 1.6, set: null };
  };

  /* PNGtuber: a layer's sprite tint (0xrrggbb) and alpha = its own x `all` */
  Avatar.prototype.pngTint = function (id) {
    var w = this.colNow.a, o = this.colNow['p:' + id];
    if (!w && !o) return 0xffffff;
    function ch(i) { return Math.round(clamp((w ? w.m[i] : 1) * (o ? o.m[i] : 1), 0, 1) * 255); }
    return (ch(0) << 16) | (ch(1) << 8) | ch(2);
  };
  Avatar.prototype.pngAlpha = function (id) {
    var w = this.colNow.a, o = this.colNow['p:' + id];
    return (w ? w.a : 1) * (o ? o.a : 1);
  };

  /* -------------------------------------------- the look: lights, outline, background echo */

  /* One filter on the avatar does all three, in a few passes over the picture of the avatar (the art, its items) that
     Pixi draws for it each frame:
       lights   any number (up to 8) of key lights (a direction over the whole model), rim lights (a line on the
                edges that face them) and spots (a round pool) - each with a colour, strength, blur and preset
       outline  a line around the art: the distance to the art in two passes (rows, then columns), at a lower
                resolution the wider it is, then a colour wherever it is within the width (blur: a soft edge)
       echo     the background's copy of the avatar: drawn from the same picture into a stage-sized texture that a
                sprite at the back of the stage shows (behind every avatar of the overlay; it is one frame behind) */

  var MAX_LIGHTS = 8, LIGHT_KIND = { key: 1, rim: 2, spot: 3 }, ECHO_STYLE = { dots: 0, lines: 1, solid: 2, glow: 3 };

  // Pixi's filter vertex shader, plus the position inside the filter area
  var LOOK_VERT = [
    'in vec2 aPosition;',
    'out vec2 vTextureCoord;',
    'out vec2 vPos;',
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
    '}'
  ].join('\n');

  // the lights and the outline, over the avatar. Positions are in the page's (logical) pixels.
  var LOOK_FRAG = [
    'in vec2 vTextureCoord;',
    'in vec2 vPos;',
    'out vec4 finalColor;',
    'uniform sampler2D uTexture;',
    'uniform sampler2D uDist;',
    'uniform vec4 uGeo;',        // the filter area: its top left, its size
    'uniform vec4 uUv;',         // uv per pixel (x, y), the area's far corner in uv
    'uniform vec4 uBody;',       // the avatar's centre, its box (w, h)
    'uniform vec4 uAmb;',        // ambient tint, amount
    'uniform vec4 uMisc;',       // flash, any light (0/1)
    'uniform vec4 uOutC;',       // outline colour, strength (0 = no outline)
    'uniform vec4 uOutG;',       // outline width, blur, the distance the field reaches, -
    'uniform vec4 uDistUv;',     // the distance field: uv per vPos
    'uniform vec4 uLA[8];',      // kind (0 off, 1 key, 2 rim, 3 spot), strength, blur, width / radius
    'uniform vec4 uLB[8];',      // colour
    'uniform vec4 uLC[8];',      // from (a direction), the spot's centre
    'float alphaAt(vec2 uv){',
    '  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > uUv.z || uv.y > uUv.w) return 0.0;',
    '  return texture(uTexture, uv).a;',
    '}',
    'void main(void){',
    '  vec4 c = texture(uTexture, vTextureCoord);',
    '  vec2 g = uGeo.xy + vPos * uGeo.zw;',
    '  vec4 lit = c;',
    '  if (c.a > 0.004 && uMisc.y > 0.5) {',
    '    vec3 base = c.rgb / c.a;',
    '    base = mix(base, base * uAmb.rgb, uAmb.a);',
    '    vec2 p = (g - uBody.xy) / max(uBody.zw, vec2(1.0));',
    '    float keys = 0.0;',
    '    vec3 lights = vec3(0.0);',
    '    vec3 rims = vec3(0.0);',
    '    for (int i = 0; i < 8; i++) {',
    '      vec4 A = uLA[i];',
    '      if (A.x < 0.5) continue;',
    '      vec3 col = uLB[i].rgb;',
    '      vec2 dir = uLC[i].xy;',
    '      if (A.x < 1.5) {',
    '        float x = dot(p, dir) * 1.6 + 0.35;',
    '        float s = clamp((x - 0.5) / (2.0 * max(0.01, A.z)) + 0.5, 0.0, 1.0);',
    '        keys += A.y;',
    '        lights += col * A.y * s * 0.75;',
    '      } else if (A.x < 2.5) {',
    '        vec2 ahead = vTextureCoord + dir * A.w * uUv.xy;',
    '        float a = 0.0;',
    '        if (A.z < 0.5) a = alphaAt(ahead);',
    '        else {',
    '          for (int k = 0; k < 12; k++) {',
    '            float fk = float(k);',
    '            float r = sqrt((fk + 0.5) / 12.0) * A.z;',
    '            float th = fk * 2.39996;',
    '            a += alphaAt(ahead + vec2(cos(th), sin(th)) * r * uUv.xy);',
    '          }',
    '          a /= 12.0;',
    '        }',
    '        rims += col * clamp(c.a - a, 0.0, 1.0) * A.y;',
    '      } else {',
    '        float d = length(g - uLC[i].zw);',
    '        float f = 1.0 - smoothstep(A.w * (1.0 - A.z) - 1.0, A.w, d);',
    '        lights += col * A.y * f * 0.75;',
    '      }',
    '    }',
    '    vec3 rgb = base * max(0.3, 1.0 - 0.35 * keys) + lights * (0.35 + base) + rims + vec3(uMisc.x);',
    '    lit = vec4(clamp(rgb, 0.0, 1.0) * c.a, c.a);',
    '  }',
    '  if (uOutC.a > 0.0) {',
    '    float d = texture(uDist, vPos * uDistUv.xy).r * uOutG.z;',
    '    float cov = 1.0 - smoothstep(uOutG.x - uOutG.y * 0.5 - 0.75, uOutG.x + uOutG.y * 0.5 + 0.75, d);',
    '    if (d > uOutG.z * 0.995) cov = 0.0;',
    '    float oa = cov * uOutC.a;',
    '    lit = lit + vec4(uOutC.rgb * oa, oa) * (1.0 - lit.a);',
    '  }',
    '  finalColor = lit;',
    '}'
  ].join('\n');

  // the outline's distance field, pass 1: along the rows, how far the nearest bit of art is (0 = on the art)
  var DIST_H_FRAG = [
    'in vec2 vTextureCoord;',
    'out vec4 finalColor;',
    'uniform sampler2D uTexture;',
    'uniform vec4 uD;',          // uv per step, steps each way, pixels per step, the reach (pixels)
    'uniform vec4 uClip;',       // the area's far corner in uv
    'float on(vec2 uv){',
    '  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > uClip.x || uv.y > uClip.y) return 0.0;',
    '  return step(0.5, texture(uTexture, uv).a);',
    '}',
    'void main(void){',
    '  float best = uD.w;',
    '  if (on(vTextureCoord) > 0.5) best = 0.0;',
    '  else for (int k = 1; k <= 32; k++) {',
    '    if (float(k) > uD.y) break;',
    '    vec2 o = vec2(float(k) * uD.x, 0.0);',
    '    if (on(vTextureCoord + o) > 0.5 || on(vTextureCoord - o) > 0.5) { best = float(k) * uD.z; break; }',
    '  }',
    '  finalColor = vec4(min(1.0, best / uD.w), 0.0, 0.0, 1.0);',
    '}'
  ].join('\n');

  // pass 2: down the columns - the nearest art anywhere (the rows' distances, and how far up / down they are)
  var DIST_V_FRAG = [
    'in vec2 vTextureCoord;',
    'out vec4 finalColor;',
    'uniform sampler2D uTexture;',
    'uniform vec4 uD;',
    'uniform vec4 uClip;',
    'float row(vec2 uv){',
    '  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > uClip.x || uv.y > uClip.y) return uD.w;',
    '  return texture(uTexture, uv).r * uD.w;',
    '}',
    'void main(void){',
    '  float best = row(vTextureCoord);',
    '  for (int k = 1; k <= 32; k++) {',
    '    if (float(k) > uD.y) break;',
    '    float dy = float(k) * uD.z;',
    '    if (dy >= best) break;',
    '    vec2 o = vec2(0.0, float(k) * uD.x);',
    '    float dx = min(row(vTextureCoord + o), row(vTextureCoord - o));',
    '    best = min(best, sqrt(dx * dx + dy * dy));',
    '  }',
    '  finalColor = vec4(min(1.0, best / uD.w), 0.0, 0.0, 1.0);',
    '}'
  ].join('\n');

  // the echo: the part of its (stage-sized) texture it covers - uRect, in 0..1 of the texture (the rest is cleared)
  var ECHO_VERT = [
    'in vec2 aPosition;',
    'out vec2 vPos;',
    'uniform vec4 uRect;',
    'void main(void){',
    '  vec2 p = uRect.xy + aPosition * uRect.zw;',
    '  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);',
    '  vPos = p;',
    '}'
  ].join('\n');

  var ECHO_FRAG = [
    'in vec2 vPos;',
    'out vec4 finalColor;',
    'uniform sampler2D uTexture;',
    'uniform vec4 uMap;',        // stage pixel -> uv of the avatar's picture: uv = s * uMap.xy + uMap.zw
    'uniform vec4 uClip;',       // the picture's far corner in uv, the stage size
    'uniform vec4 uEcho;',       // the avatar's centre (stage), the echo's scale, mirror (1 / -1)
    'uniform vec4 uOff;',        // the echo's offset (stage), style, opacity
    'uniform vec4 uMore;',       // weight (how big a dot / line gets where the art is solid), the art's own colours (0..1), -, -
    'uniform vec4 uGrid;',       // cell (stage px), cos, sin of its angle, drift
    'uniform vec4 uTone;',       // shade, fade, the echo's top and height (stage)
    'uniform vec4 uCol1;',       // colour at the top, the edge's softness (stage px: about a pixel of the texture)
    'uniform vec4 uCol2;',       // colour at the bottom
    'vec4 pic(vec2 s){',
    '  vec2 d = (s - uEcho.xy - uOff.xy) / uEcho.z;',
    '  d.x *= uEcho.w;',
    '  vec2 uv = (uEcho.xy + d) * uMap.xy + uMap.zw;',
    '  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > uClip.x || uv.y > uClip.y) return vec4(0.0);',
    '  return texture(uTexture, uv);',
    '}',
    'float tone(vec4 c, float y){',
    '  float t = c.a;',
    '  if (t > 0.004 && uTone.x > 0.0) t *= mix(1.0, 1.0 - 0.85 * dot(c.rgb / c.a, vec3(0.299, 0.587, 0.114)), uTone.x);',
    '  return t * (1.0 - uTone.y * clamp((y - uTone.z) / max(1.0, uTone.w), 0.0, 1.0));',
    '}',
    'void main(void){',
    '  vec2 s = vPos * uClip.zw;',
    '  float cell = uGrid.x, a = 0.0;',
    '  vec4 src = vec4(0.0);',                                          // the art where a dot / line / the shape takes its colour
    '  mat2 rot = mat2(uGrid.y, -uGrid.z, uGrid.z, uGrid.y);',          // stage -> grid
    '  mat2 back = mat2(uGrid.y, uGrid.z, -uGrid.z, uGrid.y);',         // grid -> stage
    '  int style = int(uOff.z + 0.5);',
    '  if (style == 0) {',
    '    vec2 q = rot * s / cell + vec2(uGrid.w / cell, 0.0);',
    '    vec2 id = floor(q) + 0.5;',
    '    vec2 cs = back * ((id - vec2(uGrid.w / cell, 0.0)) * cell);',
    '    src = pic(cs);',
    '    float t = tone(src, cs.y);',
    '    float rad = 0.5 * cell * sqrt(t) * uMore.x * 1.42;',
    '    float d = length(q - id) * cell;',
    '    float aa = uCol1.w;',
    '    a = rad < 0.25 ? 0.0 : 1.0 - smoothstep(rad - aa, rad + aa, d);',
    '  } else if (style == 1) {',
    '    vec2 q = rot * s / cell + vec2(0.0, uGrid.w / cell);',
    '    float idy = floor(q.y) + 0.5;',
    '    vec2 cs = back * (vec2(q.x, idy - uGrid.w / cell) * cell);',
    '    src = pic(cs);',
    '    float t = tone(src, cs.y);',
    '    float half_ = 0.5 * cell * t * uMore.x;',
    '    float d = abs(q.y - idy) * cell;',
    '    float aa = uCol1.w;',
    '    a = half_ < 0.2 ? 0.0 : 1.0 - smoothstep(half_ - aa, half_ + aa, d);',
    '  } else if (style == 2) {',
    '    src = pic(s);',
    '    a = tone(src, s.y);',
    '  } else {',
    '    for (int k = 0; k < 16; k++) {',
    '      float fk = float(k);',
    '      float r = sqrt((fk + 0.5) / 16.0) * cell;',
    '      float th = fk * 2.39996;',
    '      vec2 o = vec2(cos(th), sin(th)) * r;',
    '      vec4 c = pic(s + o);',
    '      src += c;',
    '      a += tone(c, s.y + o.y);',
    '    }',
    '    a /= 16.0;',
    '    src /= 16.0;',
    '  }',
    '  vec3 col = mix(uCol1.rgb, uCol2.rgb, clamp((s.y - uTone.z) / max(1.0, uTone.w), 0.0, 1.0));',
    '  if (uMore.y > 0.0 && src.a > 0.004) col = mix(col, src.rgb / src.a, uMore.y);',
    '  a *= uOff.w;',
    '  finalColor = vec4(col * a, a);',
    '}'
  ].join('\n');

  // a background's pattern, over the whole stage (a sprite the size of the stage, this as its filter)
  var PATTERN_FRAG = [
    'in vec2 vPos;',
    'out vec4 finalColor;',
    'uniform vec4 uGeo;',        // the filter area on the page: top left, size
    'uniform vec4 uRoot;',       // the stage's top left on the page, page pixels per stage pixel, the edge's softness (stage px)
    'uniform vec4 uGrid;',       // cell (stage px), cos, sin of the grid's angle, drift (stage px)
    'uniform vec4 uDir;',        // the way the colours run and the dots shrink: cos, sin; fade; kind (1 dots, 2 lines)
    'uniform vec4 uCol1;',       // first colour, weight
    'uniform vec4 uCol2;',       // second colour, opacity
    'float along(vec2 p){',      // 0 .. 1 across the stage, the way `dir` points
    '  vec2 c = vec2(960.0, 540.0);',
    '  float half_ = 0.5 * (abs(uDir.x) * 1920.0 + abs(uDir.y) * 1080.0);',
    '  return clamp(dot(p - c, uDir.xy) / max(1.0, half_) * 0.5 + 0.5, 0.0, 1.0);',
    '}',
    'void main(void){',
    '  vec2 g = uGeo.xy + vPos * uGeo.zw;',
    '  vec2 s = (g - uRoot.xy) / uRoot.z;',
    '  float cell = uGrid.x, a = 0.0, aa = uRoot.w;',
    '  mat2 rot = mat2(uGrid.y, -uGrid.z, uGrid.z, uGrid.y);',
    '  mat2 back = mat2(uGrid.y, uGrid.z, -uGrid.z, uGrid.y);',
    '  if (uDir.w < 1.5) {',
    '    vec2 q = rot * s / cell + vec2(uGrid.w / cell, 0.0);',
    '    vec2 id = floor(q) + 0.5;',
    '    vec2 cs = back * ((id - vec2(uGrid.w / cell, 0.0)) * cell);',
    '    float t = max(0.0, 1.0 - uDir.z * along(cs));',
    '    float rad = 0.5 * cell * sqrt(t) * uCol1.w * 1.42;',
    '    float d = length(q - id) * cell;',
    '    a = rad < 0.25 ? 0.0 : 1.0 - smoothstep(rad - aa, rad + aa, d);',
    '  } else {',
    '    vec2 q = rot * s / cell + vec2(0.0, uGrid.w / cell);',
    '    float idy = floor(q.y) + 0.5;',
    '    vec2 cs = back * (vec2(q.x, idy - uGrid.w / cell) * cell);',
    '    float t = max(0.0, 1.0 - uDir.z * along(cs));',
    '    float half_ = 0.5 * cell * t * uCol1.w;',
    '    float d = abs(q.y - idy) * cell;',
    '    a = half_ < 0.2 ? 0.0 : 1.0 - smoothstep(half_ - aa, half_ + aa, d);',
    '  }',
    '  vec3 col = mix(uCol1.rgb, uCol2.rgb, along(s));',
    '  a *= uCol2.w;',
    '  finalColor = vec4(col * a, a);',
    '}'
  ].join('\n');

  /* where a light is this moment, its preset played: {col, k, ang, flash}. `i` is its place in the list (the first one
     moves like the single light before 1.11 did); storms (strobe, lightning) are the same for every light of the avatar */
  function lightFrame(l, now, seed, i) {
    var col = hexRgb(l.color), k = l.intensity || 0, ang = (l.angle || 0) * Math.PI / 180, flash = 0, rim = l.kind === 'rim';
    var t = now * (l.speed || 1), sd = seed + (i || 0) * 131, ph, pn, r;
    switch (l.preset) {
      case 'pulse': k *= rim ? 0.6 + 0.4 * Math.sin(t * 2.4) : 0.55 + 0.45 * Math.sin(t * 2.4); break;
      case 'flicker': k *= 0.75 + 0.25 * noise(sd, t * 9); break;
      case 'fire': col = [1, 0.48 + 0.12 * noise(sd, t * 5), 0.12]; k *= 0.7 + 0.35 * fbm(sd, t * 7); ang = Math.PI / 2 + noise(sd + 5, t * 3) * 0.3; break;
      case 'police': ph = Math.floor(t * 3) % 2; ang = ph ? 0 : Math.PI;
        if (rim) { col = ph ? [0.2, 0.35, 1] : [1, 0.15, 0.15]; k = Math.max(k, 0.8); } else col = ph ? [1, 0.12, 0.12] : [0.15, 0.3, 1];
        break;
      case 'rainbow': col = rim ? hsv((t * 0.1 + 0.5) % 1, 0.8, 1) : hsv((t * 0.1) % 1, 0.75, 1); break;
      case 'strobe': flash = (Math.floor(t * 8) % 2) ? 0.35 : 0; break;
      case 'lightning': r = hash(seed, Math.floor(t * 4)); flash = r > 0.93 ? 0.55 * (1 - (t * 4 % 1)) : 0;
        if (rim) { col = [0.7, 0.8, 1]; k = Math.max(k, flash * 2); }
        break;
      case 'neon': pn = 0.6 + 0.4 * Math.sin(t * 1.7);
        if (rim) { col = [0.2, 0.95, 1]; k = Math.max(k, 0.9 * (1.4 - pn)); } else { col = [1, 0.25, 0.75]; k *= pn; }
        break;
    }
    return { col: col, k: k, ang: ang, flash: flash };
  }

  /* the lights as the shader takes them. geo: {s: page pixels per stage pixel, M: the avatar's matrix on the stage,
     rx, ry: the stage's top left on the page, bw, bh: the avatar's box (its own units)}; w: how far it is faded in */
  function packLights(lt, w, now, seed, geo, out) {
    var A = out.A, B = out.B, C = out.C, flash = 0, n = 0, list = (lt && lt.lights) || [];
    for (var j = 0; j < MAX_LIGHTS * 4; j++) A[j] = B[j] = C[j] = 0;
    for (var i = 0; i < list.length && n < MAX_LIGHTS; i++) {
      var l = list[i];
      if (!l || l.on === false || !LIGHT_KIND[l.kind]) continue;
      var f = lightFrame(l, now, seed, i), o = n * 4, blur = l.blur == null ? 50 : l.blur;
      A[o] = LIGHT_KIND[l.kind]; A[o + 1] = f.k * w;
      B[o] = f.col[0]; B[o + 1] = f.col[1]; B[o + 2] = f.col[2];
      C[o] = Math.cos(f.ang); C[o + 1] = Math.sin(f.ang);
      if (l.kind === 'key') A[o + 2] = blur / 100;
      else if (l.kind === 'rim') { A[o + 2] = blur / 100 * Math.max(6, l.width || 10) * 1.2 * geo.s; A[o + 3] = (l.width || 10) * geo.s; }
      else {
        var M = geo.M, lx = ((l.x == null ? 50 : l.x) - 50) / 100 * geo.bw, ly = ((l.y == null ? 30 : l.y) - 50) / 100 * geo.bh;
        var sx = M.a * lx + M.c * ly + M.tx, sy = M.b * lx + M.d * ly + M.ty;
        C[o + 2] = geo.rx + sx * geo.s; C[o + 3] = geo.ry + sy * geo.s;
        A[o + 2] = blur / 100; A[o + 3] = (l.size || 35) / 100 * geo.bh * Math.hypot(M.a, M.b) * geo.s;
      }
      flash = Math.max(flash, f.flash);
      n++;
    }
    out.flash = flash * w;
    out.n = n;
    return out;
  }
  HA.lightFrame = lightFrame;
  HA.packLights = packLights;

  function lookUniforms(spec) {
    var u = {};
    for (var k in spec) u[k] = spec[k] === 8 ? { value: new Float32Array(32), type: 'vec4<f32>', size: 8 } : { value: new Float32Array(4), type: 'vec4<f32>' };
    return new PIXI.UniformGroup(u);
  }
  function lookFilter(vert, frag, name, uniforms, extra) {
    var res = { lookU: uniforms };
    for (var k in extra || {}) res[k] = extra[k];
    return new PIXI.Filter({ glProgram: PIXI.GlProgram.from({ vertex: vert, fragment: frag, name: name }), resources: res,
                             resolution: 'inherit', padding: 2 });
  }

  /* the avatar's filter (made when something of the look is on, dropped when all of it has faded out) */
  Avatar.prototype._makeLook = function () {
    var fx = { frame: -1 };
    fx.mainU = lookUniforms({ uGeo: 1, uUv: 1, uBody: 1, uAmb: 1, uMisc: 1, uOutC: 1, uOutG: 1, uDistUv: 1, uLA: 8, uLB: 8, uLC: 8 });
    fx.main = lookFilter(LOOK_VERT, LOOK_FRAG, 'hexcast-look', fx.mainU, { uDist: PIXI.Texture.WHITE.source });
    fx.hU = lookUniforms({ uD: 1, uClip: 1 });
    fx.h = lookFilter(LOOK_VERT, DIST_H_FRAG, 'hexcast-dist-h', fx.hU);
    fx.vU = lookUniforms({ uD: 1, uClip: 1 });
    fx.v = lookFilter(LOOK_VERT, DIST_V_FRAG, 'hexcast-dist-v', fx.vU);
    fx.echoU = lookUniforms({ uRect: 1, uMap: 1, uClip: 1, uEcho: 1, uOff: 1, uMore: 1, uGrid: 1, uTone: 1, uCol1: 1, uCol2: 1 });
    fx.echo = lookFilter(ECHO_VERT, ECHO_FRAG, 'hexcast-echo', fx.echoU);
    fx.lights = { A: fx.mainU.uniforms.uLA, B: fx.mainU.uniforms.uLB, C: fx.mainU.uniforms.uLC, flash: 0, n: 0 };
    var self = this;
    fx.main.apply = function (fm, input, output, clear) { self._applyLook(fm, input, output, clear); };
    this.fx = fx;
    this.node.filters = [fx.main];
  };

  Avatar.prototype._dropLook = function () {
    var fx = this.fx;
    if (!fx) return;
    this.node.filters = null;
    ['main', 'h', 'v', 'echo'].forEach(function (k) { try { fx[k].destroy(); } catch (e) {} });
    this.fx = null;
  };

  /* every frame Pixi draws the avatar: the echo, the outline's distance field, then the avatar lit and outlined */
  Avatar.prototype._applyLook = function (fm, input, output, clear) {
    var fx = this.fx, st = this.stage, b = fm._activeFilterData && fm._activeFilterData.bounds;
    var src = input.source, fw = input.frame.width, fh = input.frame.height;
    var u = fx.mainU.uniforms, ox = b ? b.minX : 0, oy = b ? b.minY : 0;
    u.uGeo[0] = ox; u.uGeo[1] = oy; u.uGeo[2] = fw; u.uGeo[3] = fh;
    u.uUv[0] = 1 / src.width; u.uUv[1] = 1 / src.height; u.uUv[2] = fw / src.width; u.uUv[3] = fh / src.height;
    var echo = fx.echoOn && this.bg && this.bg.tex;
    if (echo) {
      var e = fx.echoU.uniforms, s = st.scale, rp = st.root.position;
      e.uMap[0] = s / src.width; e.uMap[1] = s / src.height; e.uMap[2] = (rp.x - ox) / src.width; e.uMap[3] = (rp.y - oy) / src.height;
      e.uClip[0] = fw / src.width; e.uClip[1] = fh / src.height; e.uClip[2] = W; e.uClip[3] = H;
      // where the echo can be: the picture's box (stage pixels), scaled and moved as the echo is, plus a dot / the glow
      var cx = e.uEcho[0], cy = e.uEcho[1], es = e.uEcho[2] * e.uEcho[3], pad = e.uGrid[0] + 2;
      var sx0 = (ox - rp.x) / s, sy0 = (oy - rp.y) / s, sx1 = sx0 + fw / s, sy1 = sy0 + fh / s;
      var ax = cx + e.uOff[0] + (sx0 - cx) * es, bx = cx + e.uOff[0] + (sx1 - cx) * es;
      var x0 = clamp(Math.min(ax, bx) - pad, 0, W), x1 = clamp(Math.max(ax, bx) + pad, 0, W);
      var y0 = clamp(cy + e.uOff[1] + (sy0 - cy) * e.uEcho[2] - pad, 0, H), y1 = clamp(cy + e.uOff[1] + (sy1 - cy) * e.uEcho[2] + pad, 0, H);
      e.uRect[0] = x0 / W; e.uRect[1] = y0 / H; e.uRect[2] = (x1 - x0) / W; e.uRect[3] = (y1 - y0) / H;
      fm.applyFilter(fx.echo, input, this.bg.tex, true);
      fx.frame = st.frameNo;
    }
    var pool = PIXI.TexturePool, t1 = null, t2 = null, R = fx.outR || 0;
    if (u.uOutC[3] > 0 && R > 0 && pool) {
      var ires = src._resolution || 1, steps = Math.min(32, Math.max(1, Math.ceil(R * ires))), step = R / steps;
      var dres = Math.max(0.05, Math.min(ires, 1 / step));
      t1 = pool.getOptimalTexture({ width: fw, height: fh, resolution: dres });
      t2 = pool.getOptimalTexture({ width: fw, height: fh, resolution: dres });
      var h = fx.hU.uniforms, v = fx.vU.uniforms;
      h.uD[0] = step / src.width; h.uD[1] = steps; h.uD[2] = step; h.uD[3] = R;
      h.uClip[0] = fw / src.width; h.uClip[1] = fh / src.height;
      fm.applyFilter(fx.h, input, t1, true);
      v.uD[0] = step / t1.source.height; v.uD[1] = steps; v.uD[2] = step; v.uD[3] = R;
      v.uClip[0] = fw / t1.source.width; v.uClip[1] = fh / t1.source.height;
      fm.applyFilter(fx.v, t1, t2, true);
      fx.main.resources.uDist = t2.source;
      u.uDistUv[0] = fw / t2.source.width; u.uDistUv[1] = fh / t2.source.height;
    } else {
      fx.main.resources.uDist = PIXI.Texture.WHITE.source;
      u.uOutC[3] = 0;
    }
    fm.applyFilter(fx.main, input, output, clear);
    if (t1) pool.returnTexture(t1);
    if (t2) pool.returnTexture(t2);
  };

  Avatar.prototype.boxHeight = function () { return this.png ? this.png.box.h * this.baseScale : H; };

  /* `light` (the config's, or one a bot holds for now): {enabled, ambient, ambient_amount, lights: [...]} */
  Avatar.prototype.setLight = function (lt, fade) {
    var on = lt && lt.enabled && ((lt.ambient_amount || 0) > 0 || (lt.lights || []).some(function (l) { return l && l.on !== false; }));
    this.lightCfg = on ? lt : null;
    if (on) this._lastLight = lt;
    this.lightFade = Math.max(0.01, fade == null ? 0.5 : fade);
  };

  /* `outline`: {enabled, color, width, blur, opacity} */
  Avatar.prototype.setOutline = function (o, fade) {
    var on = o && o.enabled && (o.opacity == null || o.opacity > 0) && ((o.width || 0) > 0 || (o.blur || 0) > 0);
    this.outCfg = on ? o : null;
    if (on) this._lastOut = o;
    this.outFade = Math.max(0.01, fade == null ? 0.4 : fade);
  };

  /* the background that is on: its name (one of the config's `backgrounds`), "" for none */
  Avatar.prototype.setBackground = function (name, fade) {
    var list = this.cfg.backgrounds || [], n = String(name || '').toLowerCase();
    var bg = n ? list.filter(function (b) { return b.name.toLowerCase() === n; })[0] || null : null;
    this.bgFade = Math.max(0.01, fade == null ? 0.4 : fade);
    if (bg) { this.bgCfg = bg; this._lastBg = bg; }
    else this.bgCfg = null;
    this.bgName = bg ? bg.name : '';
  };

  Avatar.prototype._lightTick = function (dt, now) {
    this.lightW = approach(this.lightW || 0, this.lightCfg ? 1 : 0, dt, (this.lightFade || 0.5) / 3);
    this.outW = approach(this.outW || 0, this.outCfg ? 1 : 0, dt, (this.outFade || 0.4) / 3);
    this.bgW = approach(this.bgW || 0, this.bgCfg ? 1 : 0, dt, (this.bgFade || 0.4) / 3);
    if (!this.lightCfg && this.lightW < 0.004) this.lightW = 0;
    if (!this.outCfg && this.outW < 0.004) this.outW = 0;
    if (!this.bgCfg && this.bgW < 0.004) this.bgW = 0;
    var bg = this.bgCfg || (this.bgW ? this._lastBg : null);
    var echoOn = !!(bg && bg.echo && bg.echo.enabled && this.bgW > 0);
    this._bgTick(bg, now, echoOn);
    if (!this.lightW && !this.outW && !echoOn) { this._dropLook(); return; }
    if (!this.fx) {
      try { this._makeLook(); } catch (e) { console.warn('avatar look', e); this._lookBroken = true; return; }
    }
    var fx = this.fx, u = fx.mainU.uniforms, st = this.stage, rp = st.root.position, s = st.scale || 1;
    var M = nodeMatrix(this.node), sc = Math.hypot(M.a, M.b);
    var geo = { s: s, M: M, rx: rp.x, ry: rp.y, bw: this.boxWidth(), bh: this.boxHeight() };
    u.uBody[0] = rp.x + M.tx * s; u.uBody[1] = rp.y + M.ty * s; u.uBody[2] = geo.bw * sc * s; u.uBody[3] = geo.bh * sc * s;
    var lt = this.lightCfg || this._lastLight;
    if (this.lightW > 0 && lt) {
      packLights(lt, this.lightW, now, this.seed, geo, fx.lights);
      var amb = hexRgb(lt.ambient);
      u.uAmb[0] = amb[0]; u.uAmb[1] = amb[1]; u.uAmb[2] = amb[2]; u.uAmb[3] = (lt.ambient_amount || 0) * this.lightW;
      u.uMisc[0] = fx.lights.flash; u.uMisc[1] = 1;
    } else {
      u.uMisc[0] = 0; u.uMisc[1] = 0;
    }
    var o = this.outCfg || this._lastOut;
    if (this.outW > 0 && o) {
      var oc = hexRgb(o.color), ow = Math.max(0, o.width || 0), ob = Math.max(0, o.blur || 0);
      u.uOutC[0] = oc[0]; u.uOutC[1] = oc[1]; u.uOutC[2] = oc[2]; u.uOutC[3] = (o.opacity == null ? 1 : o.opacity) * this.outW;
      u.uOutG[0] = ow * s; u.uOutG[1] = ob * s;
      fx.outR = (ow + ob * 0.5) * s + 2;
      u.uOutG[2] = fx.outR;
    } else {
      u.uOutC[3] = 0; fx.outR = 0;
    }
    fx.main.padding = Math.ceil(fx.outR || 0) + 2;
    fx.echoOn = echoOn;
    if (echoOn) {
      var e = fx.echoU.uniforms, ec = bg.echo, c1 = hexRgb(ec.color), c2 = hexRgb(ec.color2), ang = (ec.angle || 0) * Math.PI / 180;
      var cell = Math.max(2, ec.size || 14), drift = ((now * (ec.speed || 0)) % cell + cell) % cell;
      var es = ec.scale || 1.6, eh = geo.bh * sc * es, offx = (ec.x || 0) / 100 * W, offy = (ec.y || 0) / 100 * H;
      e.uEcho[0] = M.tx; e.uEcho[1] = M.ty; e.uEcho[2] = es; e.uEcho[3] = ec.mirror ? -1 : 1;
      e.uOff[0] = offx; e.uOff[1] = offy; e.uOff[2] = ECHO_STYLE[ec.style] || 0; e.uOff[3] = ec.opacity == null ? 0.85 : ec.opacity;
      e.uMore[0] = ec.weight == null ? 0.6 : ec.weight; e.uMore[1] = ec.art || 0;
      e.uGrid[0] = cell; e.uGrid[1] = Math.cos(ang); e.uGrid[2] = Math.sin(ang); e.uGrid[3] = drift;
      e.uTone[0] = ec.shade || 0; e.uTone[1] = ec.fade || 0; e.uTone[2] = M.ty + offy - eh / 2; e.uTone[3] = eh;
      e.uCol1[0] = c1[0]; e.uCol1[1] = c1[1]; e.uCol1[2] = c1[2]; e.uCol1[3] = 0.7 / Math.max(0.1, (this.bg && this.bg.texRes) || 1);
      e.uCol2[0] = c2[0]; e.uCol2[1] = c2[1]; e.uCol2[2] = c2[2];
    }
  };

  /* -------------------------------------------- backgrounds */

  /* What is on the stage's back layer for this avatar while a background is on: a fill (a colour, a gradient), a
     picture from the item library, and the echo (a sprite showing the texture the avatar's filter draws it into). */
  Avatar.prototype._bgTick = function (bg, now, echoOn) {
    var st = this.stage, B = this.bg;
    if (!bg || !this.bgW) { if (B) this._dropBg(); return; }
    if (!B) {
      B = this.bg = { root: new PIXI.Container(), fill: null, fillSig: '', img: null, imgId: '', imgSeq: 0, echo: null, tex: null, texRes: 0, pat: null };
      B.root.label = 'background:' + this.name;
      B.root.sortableChildren = true;                                     // fill 0, picture 1, pattern 2, echo 3
      st.backLayer.addChild(B.root);
    }
    B.root.zIndex = this.node.zIndex;
    var av = this.node.visible ? this.alpha.cur : 0, w = this.bgW;
    // the fill
    var f = bg.fill || {}, sig = f.kind === 'none' ? '' : [f.kind, f.color, f.color2, f.angle].join('|');
    if (sig !== B.fillSig) {
      if (B.fill) { B.fill.destroy({ texture: true, textureSource: true }); B.fill = null; }
      B.fillSig = sig;
      if (sig) { B.fill = new PIXI.Sprite(fillTexture(f)); B.fill.width = W; B.fill.height = H; B.fill.zIndex = 0; B.root.addChild(B.fill); }
    }
    if (B.fill) B.fill.alpha = (f.opacity == null ? 1 : f.opacity) * w * av;
    // the picture
    var im = bg.image || {}, id = im.item || '', meta = id ? st.items[id] : null;
    if (meta && meta.kind === 'live2d') meta = null;                     // (pictures only)
    if (id !== B.imgId || (B.img && !meta)) {
      if (B.img) { B.img.destroy(); B.img = null; }
      B.imgId = id;
      if (meta) this._loadBgImage(meta, ++B.imgSeq);
    }
    if (B.img) { fitPicture(B.img, im.fit); B.img.alpha = (im.opacity == null ? 1 : im.opacity) * w * av; }
    // the pattern
    var pt = bg.pattern || {};
    if (pt.kind === 'dots' || pt.kind === 'lines') {
      if (!B.pat) {
        try { B.pat = makePattern(st); B.pat.zIndex = 2; B.root.addChild(B.pat); } catch (e) { console.warn('background pattern', e); B.pat = null; }
      }
      if (B.pat) {
        var pu = B.pat.patU.uniforms, c1 = hexRgb(pt.color), c2 = hexRgb(pt.color2), ga = (pt.angle || 0) * Math.PI / 180, da = (pt.dir == null ? 90 : pt.dir) * Math.PI / 180;
        var cell = Math.max(2, pt.size || 24), sc = st.scale || 1;
        pu.uRoot[0] = st.root.position.x; pu.uRoot[1] = st.root.position.y; pu.uRoot[2] = sc;
        pu.uRoot[3] = 0.7 / Math.max(0.1, sc * ((st.app && st.app.renderer && st.app.renderer.resolution) || 1));
        pu.uGrid[0] = cell; pu.uGrid[1] = Math.cos(ga); pu.uGrid[2] = Math.sin(ga); pu.uGrid[3] = ((now * (pt.speed || 0)) % cell + cell) % cell;
        pu.uDir[0] = Math.cos(da); pu.uDir[1] = Math.sin(da); pu.uDir[2] = pt.fade || 0; pu.uDir[3] = pt.kind === 'lines' ? 2 : 1;
        pu.uCol1[0] = c1[0]; pu.uCol1[1] = c1[1]; pu.uCol1[2] = c1[2]; pu.uCol1[3] = pt.weight == null ? 0.5 : pt.weight;
        pu.uCol2[0] = c2[0]; pu.uCol2[1] = c2[1]; pu.uCol2[2] = c2[2]; pu.uCol2[3] = (pt.opacity == null ? 0.25 : pt.opacity) * w * av;
      }
    } else if (B.pat) { dropPattern(B.pat); B.pat = null; }
    // the echo
    if (echoOn) {
      var res = Math.max(0.1, Math.min(2, (st.scale || 1) * ((st.app && st.app.renderer && st.app.renderer.resolution) || 1)));
      if (!B.tex || Math.abs(B.texRes - res) > 0.01) {
        if (B.tex) B.tex.destroy(true);
        B.tex = PIXI.RenderTexture.create({ width: W, height: H, resolution: res });
        B.texRes = res;
        if (B.echo) B.echo.texture = B.tex;
      }
      if (!B.echo) { B.echo = new PIXI.Sprite(B.tex); B.echo.zIndex = 3; B.root.addChild(B.echo); }
      B.echo.width = W; B.echo.height = H;
      B.echo.alpha = w;
      // (it shows what the avatar's filter drew into it: not while the avatar is not drawn)
      B.echo.visible = this.node.visible && !!this.fx && this.fx.frame >= 0 && st.frameNo - this.fx.frame <= 2;
    } else if (B.echo) {
      B.echo.destroy(); B.echo = null;
      if (B.tex) { B.tex.destroy(true); B.tex = null; }
    }
  };

  Avatar.prototype._loadBgImage = async function (meta, seq) {
    var disp = null, B = this.bg;
    try { disp = await loadDisplay(meta, 12); } catch (e) {
      this.stage.emit('event', { event: 'error', avatar: this.name, message: 'background picture ' + meta.id + ': ' + (e && e.message || e) });
      return;
    }
    if (!disp) return;
    if (this.bg !== B || !B || B.imgSeq !== seq) { disp.destroy(); return; }
    if (disp.anchor) disp.anchor.set(0.5);
    B.img = disp;
    disp.zIndex = 1;
    B.root.addChild(disp);
  };

  Avatar.prototype._dropBg = function () {
    var B = this.bg;
    if (!B) return;
    this.bg = null;
    if (B.pat) dropPattern(B.pat);
    try { B.root.destroy({ children: true }); } catch (e) {}
    if (B.tex) { try { B.tex.destroy(true); } catch (e) {} }
  };

  /* a pattern: a white sprite the size of the stage whose filter draws the dots / lines (positions in stage pixels) */
  function makePattern(st) {
    var sp = new PIXI.Sprite(PIXI.Texture.WHITE);
    sp.width = W; sp.height = H;
    var u = lookUniforms({ uGeo: 1, uRoot: 1, uGrid: 1, uDir: 1, uCol1: 1, uCol2: 1 });
    var f = lookFilter(LOOK_VERT, PATTERN_FRAG, 'hexcast-pattern', u);
    f.apply = function (fm, input, output, clear) {
      var b = fm._activeFilterData && fm._activeFilterData.bounds, g = u.uniforms.uGeo;
      g[0] = b ? b.minX : 0; g[1] = b ? b.minY : 0; g[2] = input.frame.width; g[3] = input.frame.height;
      fm.applyFilter(f, input, output, clear);
    };
    sp.filters = [f];
    sp.patU = u;
    return sp;
  }
  function dropPattern(sp) {
    var f = sp.filters && sp.filters[0];
    sp.filters = null;
    try { sp.destroy(); if (f) f.destroy(); } catch (e) {}
  }

  // a fill: one colour, or a linear gradient from `color` to `color2` along `angle` (90 = top to bottom)
  function fillTexture(f) {
    var c = document.createElement('canvas'), x = c.getContext('2d');
    c.width = 256; c.height = 144;
    if (f.kind === 'gradient') {
      var a = (f.angle == null ? 90 : f.angle) * Math.PI / 180, dx = Math.cos(a) * 128, dy = Math.sin(a) * 72;
      var gr = x.createLinearGradient(128 - dx, 72 - dy, 128 + dx, 72 + dy);
      gr.addColorStop(0, f.color || '#000000'); gr.addColorStop(1, f.color2 || '#000000');
      x.fillStyle = gr;
    } else x.fillStyle = f.color || '#000000';
    x.fillRect(0, 0, 256, 144);
    return PIXI.Texture.from(c);
  }

  // a background picture on the stage: cover (fills it, cropped), contain (whole, letterboxed) or stretch
  function fitPicture(d, fit) {
    var tw = (d.texture && d.texture.width) || d.width || 1, th = (d.texture && d.texture.height) || d.height || 1;
    var sx = W / tw, sy = H / th, k = fit === 'contain' ? Math.min(sx, sy) : Math.max(sx, sy);
    if (fit === 'stretch') d.scale.set(sx, sy); else d.scale.set(k);
    d.position.set(W / 2, H / 2);
  }

  /* -------------------------------------------- what the API can ask for */

  Avatar.prototype.paramValues = function () {
    if (!this.model) return {};
    if (this.png) {
      var pr = this.png;
      return { state: pr.state, talking: pr.talking, speaking: pr.speaking, blinking: pr.blinking, vowel: pr.vowel, level: +pr.level.toFixed(3) };
    }
    var cm = this.model.internalModel.coreModel, out = {};
    for (var i = 0; i < this.P.ids.length; i++) out[this.P.ids[i]] = +cm.getParameterValueByIndex(i).toFixed(4);
    return out;
  };

  HA.Avatar = Avatar;
  HA.util = { clamp: clamp, ease: ease, hash: hash, noise: noise };
  HA.W = W; HA.H = H;
})();
