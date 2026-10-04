/* Test helper (tests/test_avatar.py): runs the real catalog/avatar/static/avatar_engine.js under node, with PIXI and
 * the lipsync analyzer stubbed, so an Avatar can be driven frame by frame (its real _drive / _inputLayer /
 * _finalLayer) against a fake Live2D core model: a parameter table plus get/set by index. */
'use strict';
const fs = require('fs'), vm = require('vm');

function load(file) {
  const env = { clock: 1.7e9 };
  class C {
    constructor() { this.children = []; this.style = {}; this.position = { set() {} }; this.scale = { set() {} }; }
    addChild() {} addChildAt() {} removeChild() {} destroy() {}
  }
  const sb = { console, window: {}, document: {}, setTimeout, clearTimeout,
    performance: { now: () => env.clock * 1000, timeOrigin: 0 }, PIXI: new Proxy({}, { get: () => C }) };
  sb.window.HexLipsync = { Analyzer: function () {
    this.out = { level: 0, open: 0, form: 0, volume: 0, frequency: 0.5, silence: 1, A: 0, I: 0, U: 0, E: 0, O: 0 };
    this.update = () => this.out; this.reset = () => {}; this.configure = () => {}; this.attachAnalyser = () => {}; } };
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(file, 'utf8'), sb);
  return { HA: sb.window.HexAvatar, env };
}

function bare(HA, idle) {
  const stage = { root: { addChild() {} }, emit() {}, role: 'obs' };
  return new HA.Avatar(stage, { name: 'tester', x: 50, y: 50, scale: 1, rotation: 0, flip: false, visible: true,
                                idle: idle || {}, mouth: { lipsync: false } });
}

function attach(av, spec) {                         // the model "loads": parameters, mappings
  const vals = spec.params.map(p => p.def);
  const cm = { getParameterValueByIndex: i => vals[i], setParameterValueByIndex: (i, v) => { vals[i] = v; } };
  av.P = { ids: spec.params.map(p => p.id), index: Object.create(null), min: spec.params.map(p => p.min),
           max: spec.params.map(p => p.max), def: spec.params.map(p => p.def) };
  spec.params.forEach((p, i) => { av.P.index[p.id] = i; });
  av.Parts = { ids: [], index: Object.create(null) };
  av.model = { internalModel: { coreModel: cm, motionManager: { state: {}, stopAllMotions() {} } } };
  av.kind = 'live2d';
  av.mset = { mappings: spec.mappings };
  av.partCurves = []; av.restPartOpacity = null;
  av._compile();
  return { vals, cm };
}

function rig(file, spec, idle) {
  const { HA, env } = load(file);
  const av = bare(HA, idle);
  const m = attach(av, spec);
  const step = (dt) => { dt = dt || 1 / 60; env.clock += dt; av._drive(dt, env.clock); av._inputLayer(m.cm); av._finalLayer(m.cm); };
  const val = (id) => m.vals[av.P.index[id]];
  return { av, env, step, val, vals: m.vals, HA };
}

const P = (id, min, max, def) => ({ id, min, max, def });
const row = (input, output, i0, i1, o0, o1, smoothing, extra) =>
  Object.assign({ name: output, input, output, in: [i0, i1], out: [o0, o1], clamp_in: true, clamp_out: true, smoothing: smoothing || 0, blink: false, breath: false }, extra || {});

// a model with only VTube Studio's standard inputs
const STD = {
  params: [P('ParamAngleX', -30, 30, 0), P('ParamAngleY', -30, 30, 0), P('ParamAngleZ', -30, 30, 0), P('ParamEyeLOpen', 0, 1, 1),
           P('ParamEyeROpen', 0, 1, 1), P('ParamMouthForm', -1, 1, 0), P('ParamMouthOpenY', 0, 1, 0), P('ParamBreath', 0, 1, 0), P('ParamCheek', 0, 1, 0)],
  mappings: [row('FaceAngleX', 'ParamAngleX', -30, 30, -30, 30, 15), row('FaceAngleY', 'ParamAngleY', -30, 30, -30, 30, 15),
             row('FaceAngleZ', 'ParamAngleZ', -30, 30, -30, 30, 15),
             row('EyeOpenLeft', 'ParamEyeLOpen', 0, 1, 0, 1, 10, { blink: true }), row('EyeOpenRight', 'ParamEyeROpen', 0, 1, 0, 1, 10, { blink: true }),
             row('MouthSmile', 'ParamMouthForm', 0, 1, -1, 1, 0), row('MouthOpen', 'ParamMouthOpenY', 0, 1, 0, 1, 0),
             row('', 'ParamBreath', 0, 1, 0, 1, 0, { breath: true })],
};

module.exports = { load, bare, attach, rig, P, row, STD };
