/* Hexcast Avatars - lipsync analysis (window.HexLipsync).
 *
 * Turns voice audio into VTube Studio's lipsync inputs: VoiceVolume, VoiceSilence, VoiceFrequency
 * and the vowel weights VoiceA / VoiceI / VoiceU / VoiceE / VoiceO - plus the plain MouthOpen and
 * mouth-form values a model without vowel shapes uses.
 *
 * Vowels come from the formants of the voice (F1 = how open the jaw is, F2 = spread vs rounded
 * lips), found with LPC (linear prediction) on each ~30 ms window. Formants differ from voice to
 * voice, so they are normalised to the speaker: by default from a running estimate that settles in
 * after a few sentences, or from "learn this voice" (a sample run through learnVoice()). Nothing is
 * trained, nothing is sent anywhere, and a frame costs a few thousand multiplications.
 *
 * Audio arrives two ways: an AnalyserNode on speech the page plays (attachAnalyser), or PCM chunks
 * pushed in (push) - the device stream Hexcast records.
 */
(function () {
  'use strict';

  var VOWELS = ['A', 'I', 'U', 'E', 'O'];
  // Where each vowel sits in speaker-normalised formant space (z-scores of F1, F2).
  var PROTO = { A: [1.35, 0.05], I: [-1.1, 1.45], U: [-1.0, -0.55], E: [0.25, 0.95], O: [0.3, -1.15] };
  // How open the mouth is, and how spread (+) or rounded (-), for each vowel.
  var OPEN = { A: 1.0, I: 0.42, U: 0.38, E: 0.66, O: 0.74 };
  var FORM = { A: 0.05, I: 0.6, U: -0.4, E: 0.35, O: -0.5 };
  // A neutral speaker before anything is known (between typical adult voices).
  var START = { m1: 560, s1: 170, m2: 1650, s2: 430 };

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  function Analyzer() {
    this.cfg = { gain: 1, cutoff: 0.08, vowels: true, smoothing: 45, open: 1, voice: null };
    this.ring = new Float32Array(16384);
    this.ringPos = 0;
    this.ringRate = 16000;
    this.ringFill = 0;
    this.lastPush = 0;
    this.analyser = null;
    this.abuf = null;
    this.stats = { m1: START.m1, s1: START.s1, m2: START.m2, s2: START.s2, n: 0 };
    this.out = { volume: 0, silence: 1, frequency: 0.5, A: 0, I: 0, U: 0, E: 0, O: 0, open: 0, form: 0,
                 f1: 0, f2: 0, voiced: 0, level: 0, raw: 0 };
    this._raw = { A: 0, I: 0, U: 0, E: 0, O: 0 };
    this._win = null;
    this._sm = { level: 0, open: 0, form: 0, A: 0, I: 0, U: 0, E: 0, O: 0, freq: 0.5, voiced: 0 };
  }

  Analyzer.prototype.configure = function (c) {
    c = c || {};
    var v = this.cfg;
    if (c.gain != null) v.gain = +c.gain;
    if (c.cutoff != null) v.cutoff = +c.cutoff;
    if (c.vowels != null) v.vowels = !!c.vowels;
    if (c.smoothing != null) v.smoothing = +c.smoothing;
    if (c.open != null) v.open = +c.open;
    var cal = c.calibration;
    v.voice = cal && cal.m1 && cal.s1 && cal.m2 && cal.s2 ? cal : null;
  };

  Analyzer.prototype.attachAnalyser = function (node) {
    this.analyser = node;
    this.abuf = node ? new Float32Array(node.fftSize) : null;
  };

  /* PCM from the device stream: Int16 or Float32 samples at `rate`. */
  Analyzer.prototype.push = function (samples, rate) {
    if (rate && rate !== this.ringRate) { this.ringRate = rate; this.ringFill = 0; }
    var r = this.ring, n = r.length, p = this.ringPos, scale = samples instanceof Int16Array ? 1 / 32768 : 1;
    for (var i = 0; i < samples.length; i++) { r[p] = samples[i] * scale; p = (p + 1) % n; }
    this.ringPos = p;
    this.ringFill = Math.min(n, this.ringFill + samples.length);
    this.lastPush = performance.now();
  };

  Analyzer.prototype.reset = function () {
    this.ringFill = 0;
    var o = this.out, s = this._sm;
    o.volume = o.open = o.form = o.level = o.voiced = o.raw = 0; o.silence = 1;
    for (var i = 0; i < 5; i++) { o[VOWELS[i]] = 0; s[VOWELS[i]] = 0; }
    s.level = s.open = s.form = s.voiced = 0;
  };

  /* The latest window of audio as Float32 + its sample rate, or null when nothing is playing. */
  Analyzer.prototype._window = function () {
    if (this.analyser) {
      this.analyser.getFloatTimeDomainData(this.abuf);
      return { x: this.abuf, rate: this.analyser.context.sampleRate };
    }
    if (!this.ringFill || performance.now() - this.lastPush > 250) return null;
    var len = Math.min(this.ringFill, Math.round(this.ringRate * 0.032));
    if (!this._win || this._win.length !== len) this._win = new Float32Array(len);
    var r = this.ring, n = r.length, start = (this.ringPos - len + n) % n;
    for (var i = 0; i < len; i++) this._win[i] = r[(start + i) % n];
    return { x: this._win, rate: this.ringRate };
  };

  /* One analysis step (call every frame). dt in seconds. Returns this.out. */
  Analyzer.prototype.update = function (dt) {
    var w = this._window(), o = this.out;
    var feat = w ? analyse(w.x, w.rate) : null;
    this._apply(feat, dt || 1 / 60);
    return o;
  };

  Analyzer.prototype._apply = function (feat, dt) {
    var c = this.cfg, o = this.out, sm = this._sm;
    var level = 0, voiced = 0, z1 = 0, z2 = 0;
    if (feat) {
      // level: rms in dB through the gain, -50 dB -> 0, -12 dB -> 1
      level = clamp((20 * Math.log10(feat.rms * c.gain + 1e-9) + 50) / 38, 0, 1);
      if (level < c.cutoff) level = 0;
      else level = (level - c.cutoff) / (1 - c.cutoff);
      voiced = feat.voiced;
      if (feat.f1 > 0 && voiced > 0.45 && level > 0.05) {
        var st = c.voice || this.stats;
        if (!c.voice) {                                   // follow the speaker (slowly)
          var k = this.stats.n < 60 ? 0.05 : 0.008;
          var s = this.stats;
          s.m1 += (feat.f1 - s.m1) * k; s.m2 += (feat.f2 - s.m2) * k;
          s.s1 += (Math.abs(feat.f1 - s.m1) * 1.25 - s.s1) * k;
          s.s2 += (Math.abs(feat.f2 - s.m2) * 1.25 - s.s2) * k;
          s.s1 = clamp(s.s1, 70, 400); s.s2 = clamp(s.s2, 180, 900); s.n++;
        }
        z1 = (feat.f1 - st.m1) / st.s1;
        z2 = (feat.f2 - st.m2) / st.s2;
        o.f1 = feat.f1; o.f2 = feat.f2;
      }
    }
    // vowel weights from the distance to each vowel's spot
    var raw = this._raw, total = 0, i, v;
    if (c.vowels && level > 0 && voiced > 0.3) {
      for (i = 0; i < 5; i++) {
        v = VOWELS[i];
        var d1 = z1 - PROTO[v][0], d2 = z2 - PROTO[v][1];
        raw[v] = Math.exp(-(d1 * d1 + d2 * d2) / 1.1);
        total += raw[v];
      }
    }
    var open = 0, form = 0;
    for (i = 0; i < 5; i++) {
      v = VOWELS[i];
      raw[v] = total > 0 ? raw[v] / total : 0;
      open += raw[v] * OPEN[v];
      form += raw[v] * FORM[v];
    }
    if (!c.vowels || total === 0) { open = level > 0 ? 0.7 : 0; form = 0; }
    // consonants (s, sh, f ...) keep the mouth a little open, never wide
    var openNow = level * (0.35 + 0.65 * voiced) * (0.35 + 0.75 * open) * c.open;
    // attack quickly, release a bit slower; `smoothing` 0..100 -> 12..140 ms
    var tau = 0.012 + c.smoothing / 100 * 0.128;
    var up = 1 - Math.exp(-dt / (tau * 0.55)), down = 1 - Math.exp(-dt / tau);
    function ease(key, target) { var r = target > sm[key] ? up : down; sm[key] += (target - sm[key]) * r; return sm[key]; }
    o.raw = level;                                         // what this window said, before the smoothing: a PNGtuber's mouth follows it
    o.level = ease('level', level);
    o.open = clamp(ease('open', openNow), 0, 1.5);
    o.form = ease('form', level > 0 ? form : 0);
    o.voiced = ease('voiced', voiced);
    var vsum = 0;
    for (i = 0; i < 5; i++) { v = VOWELS[i]; o[v] = clamp(ease(v, raw[v] * Math.min(1, level * 1.6)), 0, 1); vsum += o[v]; }
    if (vsum > 1) for (i = 0; i < 5; i++) o[VOWELS[i]] /= vsum;
    o.volume = o.level;
    o.silence = clamp(1 - o.level * 4, 0, 1);
    o.frequency = ease('freq', level > 0 ? clamp(0.5 + z2 * 0.22, 0, 1) : 0.5);
    return o;
  };

  /* ---------------- DSP ---------------- */

  var _dec = null, _r = new Float64Array(32), _a = new Float64Array(32), _tmp = new Float64Array(32);

  /* rms, voicing and the first two formants of one window */
  function analyse(x, rate) {
    var n = x.length, i;
    var sum = 0;
    for (i = 0; i < n; i++) sum += x[i] * x[i];
    var rms = Math.sqrt(sum / Math.max(1, n));
    if (rms < 1e-4) return { rms: rms, voiced: 0, f1: 0, f2: 0 };
    // decimate to ~11-16 kHz (formants live under 4 kHz) with a box filter
    var d = Math.max(1, Math.round(rate / 12000)), m = Math.floor(n / d), fs = rate / d;
    if (!_dec || _dec.length !== m) _dec = new Float64Array(m);
    var zc = 0, prev = 0;
    for (i = 0; i < m; i++) {
      var s = 0;
      for (var k = 0; k < d; k++) s += x[i * d + k];
      s /= d;
      _dec[i] = s;
      if ((s >= 0) !== (prev >= 0)) zc++;
      prev = s;
    }
    var zcr = zc / m * (fs / 12000);
    var voiced = clamp(1 - (zcr - 0.14) / 0.22, 0, 1);
    // pre-emphasis + Hamming
    var last = _dec[0];
    for (i = m - 1; i > 0; i--) _dec[i] = _dec[i] - 0.94 * _dec[i - 1];
    _dec[0] = last * 0.06;
    for (i = 0; i < m; i++) _dec[i] *= 0.54 - 0.46 * Math.cos(2 * Math.PI * i / (m - 1));
    // LPC by autocorrelation + Levinson-Durbin
    var p = Math.min(18, Math.max(8, Math.round(fs / 1000) + 2));
    for (var lag = 0; lag <= p; lag++) {
      var acc = 0;
      for (i = lag; i < m; i++) acc += _dec[i] * _dec[i - lag];
      _r[lag] = acc;
    }
    if (_r[0] <= 0) return { rms: rms, voiced: 0, f1: 0, f2: 0 };
    _r[0] *= 1.0001;
    var err = _r[0];
    for (i = 0; i <= p; i++) _a[i] = 0;
    _a[0] = 1;
    for (i = 1; i <= p; i++) {
      var acc2 = _r[i];
      for (var j = 1; j < i; j++) acc2 -= _a[j] * _r[i - j];
      var kk = acc2 / err;
      for (j = 1; j < i; j++) _tmp[j] = _a[j] - kk * _a[i - j];
      for (j = 1; j < i; j++) _a[j] = _tmp[j];
      _a[i] = kk;
      err *= (1 - kk * kk);
      if (err <= 0) break;
    }
    // the envelope 1/|A(e^jw)| on a grid from 150 Hz to 3.6 kHz; its first two peaks are F1 and F2
    var K = 112, f0 = 150, f1max = Math.min(3600, fs / 2 - 100), step = (f1max - f0) / (K - 1);
    var env = _env(K), peaks = [];
    for (var q = 0; q < K; q++) {
      var w = 2 * Math.PI * (f0 + q * step) / fs, re = 1, im = 0;
      for (j = 1; j <= p; j++) { re -= _a[j] * Math.cos(w * j); im += _a[j] * Math.sin(w * j); }
      env[q] = 1 / (re * re + im * im + 1e-12);
    }
    for (q = 1; q < K - 1; q++) {
      if (env[q] > env[q - 1] && env[q] >= env[q + 1]) {
        // parabolic refinement of the peak position
        var y0 = Math.log(env[q - 1]), y1 = Math.log(env[q]), y2 = Math.log(env[q + 1]);
        var den = y0 - 2 * y1 + y2, off = den !== 0 ? 0.5 * (y0 - y2) / den : 0;
        peaks.push(f0 + (q + clamp(off, -0.5, 0.5)) * step);
      }
    }
    var F1 = 0, F2 = 0;
    for (q = 0; q < peaks.length; q++) {
      if (!F1 && peaks[q] >= 220 && peaks[q] <= 1150) F1 = peaks[q];
      else if (F1 && !F2 && peaks[q] > F1 + 250 && peaks[q] <= 3300) F2 = peaks[q];
    }
    if (F1 && !F2) {                                       // a merged F1/F2 (back vowels): F2 just above
      F2 = Math.min(3300, F1 * 1.6 + 250);
    }
    return { rms: rms, voiced: voiced, f1: F1, f2: F2 };
  }
  var _envBuf = null;
  function _env(K) { if (!_envBuf || _envBuf.length !== K) _envBuf = new Float64Array(K); return _envBuf; }

  /* Learn a voice from a recording (Float32 mono, rate): the speaker statistics the analysis
     normalises with. Returns {m1, s1, m2, s2, frames} or null when there is too little speech. */
  function learnVoice(samples, rate) {
    var hop = Math.round(rate * 0.012), win = Math.round(rate * 0.032), f1s = [], f2s = [];
    var buf = new Float32Array(win);
    for (var start = 0; start + win <= samples.length; start += hop) {
      for (var i = 0; i < win; i++) buf[i] = samples[start + i];
      var f = analyse(buf, rate);
      if (f.f1 && f.voiced > 0.55 && f.rms > 0.01) { f1s.push(f.f1); f2s.push(f.f2); }
    }
    if (f1s.length < 80) return null;
    function robust(a) {
      a = a.slice().sort(function (x, y) { return x - y; });
      var med = a[Math.floor(a.length / 2)], q1 = a[Math.floor(a.length / 4)], q3 = a[Math.floor(a.length * 3 / 4)];
      return [med, Math.max(1, (q3 - q1) / 1.35)];
    }
    var r1 = robust(f1s), r2 = robust(f2s);
    return { m1: Math.round(r1[0]), s1: Math.round(clamp(r1[1], 60, 400)), m2: Math.round(r2[0]),
             s2: Math.round(clamp(r2[1], 160, 900)), frames: f1s.length };
  }

  window.HexLipsync = { Analyzer: Analyzer, analyse: analyse, learnVoice: learnVoice, VOWELS: VOWELS };
})();
