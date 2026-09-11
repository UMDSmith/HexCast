// Shared WebGL chroma keyer, used by both the OBS overlay and the control-panel
// editor preview. Draws a <video>'s frames (or a static <img>) onto a transparent
// canvas with the key color removed, so keying happens per-clip BEFORE clips
// composite over each other (an OBS chroma filter keys the already-flattened
// browser source, which punches holes in clips underneath).
//
//   const keyer = HexChroma.attach(videoOrImg, { color: "#00ff00", tolerance: 0.3 });
//   parent.appendChild(keyer.canvas);   // style/position the canvas, hide the source
//   keyer.update({ tolerance: 0.5 });   // live re-tune
//   keyer.destroy();                    // stop the render loop, free the context
//
// Returns null when WebGL is unavailable — callers fall back to the plain <video>.
(function () {
  "use strict";

  const VERT = `
    attribute vec2 a_pos;
    varying vec2 v_uv;
    void main() {
      v_uv = a_pos * 0.5 + 0.5;
      gl_Position = vec4(a_pos, 0.0, 1.0);
    }`;

  // Key on distance in UV (chroma) space so any brightness of the key color is
  // removed alike. Spill suppression then pulls the key hue out of surviving
  // pixels near the threshold — edge pixels are a *blend* of subject and key
  // (soft edges, motion blur, 4:2:0 chroma smearing), so their color sits
  // between the two and no tolerance can cut them without eating the subject.
  // Neutralizing the key component turns the green halo gray instead.
  const FRAG = `
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_tex;
    uniform vec2 u_key;     // key color in UV space
    uniform float u_tol;    // similarity threshold (UV distance)
    uniform float u_soft;   // width of the opaque/transparent transition band

    vec2 rgb2uv(vec3 c) {
      return vec2(dot(c, vec3(-0.169, -0.331,  0.500)),
                  dot(c, vec3( 0.500, -0.419, -0.081)));
    }

    void main() {
      vec4 c = texture2D(u_tex, v_uv);
      vec2 uv = rgb2uv(c.rgb);
      float dist = distance(uv, u_key);
      float alpha = smoothstep(u_tol, u_tol + u_soft, dist);

      vec3 rgb = c.rgb;
      float keyLen = length(u_key);
      // Spill strength: full just past the key threshold, fading to nothing by
      // ~2× the keyed radius so genuinely key-colored parts of the subject
      // farther out keep their saturation.
      float spill = 1.0 - smoothstep(u_tol, u_tol * 2.0 + u_soft * 2.0 + 0.02, dist);
      if (keyLen > 0.001 && spill > 0.0) {
        vec2 keyDir = u_key / keyLen;
        float proj = dot(uv, keyDir);
        if (proj > 0.0) {
          vec2 uv2 = uv - keyDir * proj * spill;
          float y = dot(c.rgb, vec3(0.299, 0.587, 0.114));
          rgb = vec3(y + 1.402 * uv2.y,
                     y - 0.344 * uv2.x - 0.714 * uv2.y,
                     y + 1.772 * uv2.x);
        }
      }
      gl_FragColor = vec4(clamp(rgb, 0.0, 1.0) * alpha, alpha);  // premultiplied
    }`;

  function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
    const n = m ? parseInt(m[1], 16) : 0x00ff00;
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }

  function rgbToUv(r, g, b) {
    return [-0.169 * r - 0.331 * g + 0.500 * b,
             0.500 * r - 0.419 * g - 0.081 * b];
  }

  function compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      console.error("chroma shader:", gl.getShaderInfoLog(s));
      return null;
    }
    return s;
  }

  function attach(source, opts) {
    opts = opts || {};
    const isVideoSrc = source.tagName === "VIDEO";
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: true });
    if (!gl) return null;

    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return null;
    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.error("chroma link:", gl.getProgramInfoLog(prog));
      return null;
    }
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, "a_pos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);

    const uKey = gl.getUniformLocation(prog, "u_key");
    const uTol = gl.getUniformLocation(prog, "u_tol");
    const uSoft = gl.getUniformLocation(prog, "u_soft");

    let needsDraw = true;   // images only redraw when the key settings change

    function setOptions(o) {
      needsDraw = true;
      if (o.color !== undefined) {
        const [r, g, b] = hexToRgb(o.color);
        const uv = rgbToUv(r, g, b);
        gl.uniform2f(uKey, uv[0], uv[1]);
      }
      if (o.tolerance !== undefined) {
        // tolerance 0..1 → UV distance 0..0.4 (0.4 keys almost everything).
        const t = Math.max(0, Math.min(1, +o.tolerance || 0)) * 0.4;
        gl.uniform1f(uTol, t);
        gl.uniform1f(uSoft, Math.max(0.015, t * 0.3));
      }
    }
    setOptions({ color: opts.color || "#00ff00",
                 tolerance: opts.tolerance !== undefined ? opts.tolerance : 0.3 });

    let destroyed = false;
    let wasConnected = false;

    function draw() {
      const w = isVideoSrc ? source.videoWidth : source.naturalWidth;
      const h = isVideoSrc ? source.videoHeight : source.naturalHeight;
      const ready = isVideoSrc ? (source.readyState >= 2 && w) : (source.complete && w);
      if (!ready) return false;
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        gl.viewport(0, 0, w, h);
      }
      gl.bindTexture(gl.TEXTURE_2D, tex);
      try {
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      } catch (_) { return false; }
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      return true;
    }

    function loop() {
      if (destroyed) return;
      // Self-clean if the canvas was pulled out of the DOM without destroy()
      // (e.g. the overlay's stopAll wipes the stage with innerHTML = ""). Our
      // closure keeps the orphaned video alive, so pause it or its audio lingers.
      if (canvas.isConnected) wasConnected = true;
      else if (wasConnected) {
        if (isVideoSrc && !source.isConnected) { try { source.pause(); } catch (_) {} }
        destroy();
        return;
      }
      if (isVideoSrc || needsDraw) {
        if (draw()) needsDraw = false;
      }
      schedule();
    }

    // rAF (not requestVideoFrameCallback) so paused frames still repaint —
    // the editor scrubs a paused video and needs live updates.
    let rafId = 0;
    function schedule() { rafId = requestAnimationFrame(loop); }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      cancelAnimationFrame(rafId);
      const ext = gl.getExtension("WEBGL_lose_context");
      if (ext) ext.loseContext();
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    }

    schedule();
    return { canvas: canvas, update: setOptions, destroy: destroy };
  }

  window.HexChroma = { attach: attach };
})();
