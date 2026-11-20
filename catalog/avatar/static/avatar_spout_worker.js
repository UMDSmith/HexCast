/* Hexcast Avatars - Spout output, the sending half (a Web Worker of the hidden page that draws an overlay for Spout).
 *
 * The page draws a frame, reads it back from the graphics card and hands the bytes over here (transferred, not copied).
 * Here they are compared with the last frame sent and only what changed goes to Hexcast over a websocket - on this
 * thread, so the socket is fed while the page draws the next frame. The buffers go back to the page to be read into again.
 *
 * What goes over the socket: after a first whole frame, only what changed since the last frame sent - the frame is cut into
 * bands of rows, each band's changed columns found, and neighbouring bands with about the same columns merged into one
 * rectangle. A message: "HXS1", the number of rectangles (u16) and 0 (u16), then x, y, w, h (u16 each) per rectangle, then
 * each rectangle's pixels (w x h x 4 bytes, rows top first), all little-endian. Nothing changed: no message.
 *
 * Page -> worker: {type: "open", url, w, h, fps}, {type: "frame", buf} (a Uint8Array of w x h x 4, transferred)
 * Worker -> page: {type: "buf", buf} (one to read into again, transferred), {type: "busy", busy}
 */
'use strict';

function SpoutPacker(w, h, band) {
  this.w = w; this.h = h; this.band = band || 24;
  this.sent = null;                                  // the last frame sent (this keeps it to compare the next one with)
  this.out = new ArrayBuffer(8 + 8 * Math.ceil(h / this.band) + w * h * 4);
  this.full = true;
}
SpoutPacker.prototype.forceFull = function () { this.full = true; };
SpoutPacker.prototype.rects = function (frame) {
  var w = this.w, h = this.h, out = [];
  if (this.full || !this.sent) return [[0, 0, w, h]];
  var cur = new Uint32Array(frame.buffer, frame.byteOffset, w * h), old = new Uint32Array(this.sent.buffer, this.sent.byteOffset, w * h);
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
/* {msg, free}: the message for this frame (null when nothing changed) and the buffer that is free again - the frame that was
   the last one sent (this frame takes its place), or this frame itself when nothing went out */
SpoutPacker.prototype.pack = function (frame) {
  var rects = this.rects(frame);
  if (!rects.length) return { msg: null, free: frame };
  var dv = new DataView(this.out), u8 = new Uint8Array(this.out), w = this.w, p = 8 + 8 * rects.length, i, k;
  dv.setUint8(0, 72); dv.setUint8(1, 88); dv.setUint8(2, 83); dv.setUint8(3, 49);              // "HXS1"
  dv.setUint16(4, rects.length, true); dv.setUint16(6, 0, true);
  for (i = 0; i < rects.length; i++) {
    var r = rects[i];
    for (k = 0; k < 4; k++) dv.setUint16(8 + i * 8 + k * 2, r[k], true);
    if (r[0] === 0 && r[2] === w) {                                // whole rows: one copy
      u8.set(frame.subarray(r[1] * w * 4, (r[1] + r[3]) * w * 4), p);
      p += r[2] * r[3] * 4;
      continue;
    }
    for (var y = r[1]; y < r[1] + r[3]; y++) {
      var a = (y * w + r[0]) * 4, n = r[2] * 4;
      u8.set(frame.subarray(a, a + n), p);
      p += n;
    }
  }
  var free = this.sent;
  this.sent = frame;
  this.full = false;
  return { msg: new Uint8Array(this.out, 0, p), free: free };
};

if (typeof module !== 'undefined' && module.exports) module.exports = { SpoutPacker: SpoutPacker };

if (typeof importScripts === 'function') (function () {
  var ws = null, conf = null, packer = null, wait = 500, lastFull = 0, lastSize = 2e6, busy = false;
  function give(buf) { if (buf) self.postMessage({ type: 'buf', buf: buf }, [buf.buffer]); }
  function setBusy(b) { if (b !== busy) { busy = b; self.postMessage({ type: 'busy', busy: b }); } }
  // up to about two frames on their way: the socket never waits for the next one
  function full() { return !ws || ws.readyState !== 1 || ws.bufferedAmount > Math.max(2e6, 1.2 * lastSize); }
  function open() {
    ws = new WebSocket(conf.url);
    ws.binaryType = 'arraybuffer';
    ws.onopen = function () { wait = 500; packer.forceFull(); ws.send(JSON.stringify({ type: 'hello', w: conf.w, h: conf.h, fps: conf.fps })); };
    ws.onmessage = function (ev) { if (typeof ev.data === 'string' && ev.data.indexOf('"full"') >= 0) packer.forceFull(); };   // Hexcast lost track
    ws.onclose = function () { ws = null; setBusy(true); setTimeout(open, wait); wait = Math.min(8000, wait * 2); };
  }
  setInterval(function () { setBusy(full()); }, 4);              // (the page asks for frames again as soon as there is room)
  self.onmessage = function (ev) {
    var m = ev.data;
    if (m.type === 'open') {
      conf = m;
      packer = new SpoutPacker(m.w, m.h);
      open();
    } else if (m.type === 'error') {                               // (the page could not start: Hexcast shows why)
      (function tell() {
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'error', message: m.message }));
        else setTimeout(tell, 1000);
      })();
    } else if (m.type === 'frame') {
      if (!packer || full()) { setBusy(true); give(m.buf); return; }      // (the socket still busy: this frame is skipped)
      var now = Date.now();
      if (now - lastFull > 5000) { packer.forceFull(); lastFull = now; }   // a whole frame now and then, to be safe
      var r = packer.pack(m.buf);
      if (r.msg) { ws.send(r.msg); lastSize = r.msg.byteLength; }
      give(r.free);
      setBusy(full());
    }
  };
})();
