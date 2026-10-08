/* Test helper (tests/test_avatar.py): expressions and held API values on parameters nothing else drives. Runs the real
 * avatar_engine.js under node against a fake Cubism model that does what the engine's update really does with parameters:
 *   motions -> afterMotionUpdate (the input layer: mappings, expressions, held values) -> SAVE the parameters ->
 *   physics ... -> beforeModelUpdate (the final layer) -> update -> LOAD (the saved ones come back)
 * so what the input layer adds on top of a parameter is the next frame's start unless the engine puts it back. Prints JSON:
 *   node avatar_engine_expr.js <path to avatar_engine.js> */
'use strict';
const { load, bare, P, row, STD } = require('./avatar_engine_harness.js');

const file = process.argv[2];
const SPEC = {
  params: STD.params.concat([P('WingFlap', -1, 1, 0), P('ToeTap', -30, 30, 0), P('ArmWiggle', -30, 30, 0)]),
  mappings: STD.mappings,
};

function model(hooked) {
  const { HA, env } = load(file);
  const av = bare(HA, { blink: false });
  const n = SPEC.params.length, vals = new Float32Array(SPEC.params.map((p) => p.def)), saved = new Float32Array(vals);
  const cm = {
    getParameterValueByIndex: (i) => vals[i],
    setParameterValueByIndex: (i, v) => { vals[i] = Math.min(SPEC.params[i].max, Math.max(SPEC.params[i].min, v)); },
    saveParameters() { saved.set(vals); }, loadParameters() { vals.set(saved); },
  };
  av.P = { ids: SPEC.params.map((p) => p.id), index: Object.create(null), min: SPEC.params.map((p) => p.min), max: SPEC.params.map((p) => p.max), def: SPEC.params.map((p) => p.def) };
  SPEC.params.forEach((p, i) => { av.P.index[p.id] = i; });
  av.Parts = { ids: [], index: Object.create(null) };
  av.model = { internalModel: { coreModel: cm, motionManager: { state: {}, stopAllMotions() {} } } };
  av.kind = 'live2d'; av.mset = { mappings: SPEC.mappings }; av.partCurves = []; av.restPartOpacity = null;
  av._compile();
  if (hooked) av._hookLoad(cm);
  const seen = {};                                          // what the model is drawn with: the values just before its update
  const frame = (motion) => {
    env.clock += 1 / 60;
    av._drive(1 / 60, env.clock);
    if (motion) Object.keys(motion).forEach((k) => cm.setParameterValueByIndex(av.P.index[k], motion[k]));        // a motion animating a parameter
    av._inputLayer(cm);                                     // afterMotionUpdate
    cm.saveParameters();
    av._finalLayer(cm);                                     // beforeModelUpdate
    SPEC.params.forEach((p, i) => { seen[p.id] = Math.round(vals[i] * 1000) / 1000; });
    cm.loadParameters();                                    // the end of the update
  };
  const run = (secs, motion) => { for (let i = 0; i < Math.round(secs * 60); i++) frame(motion); return Object.assign({}, seen); };
  const expr = (name, data) => { av.exprs[name] = { w: 0, to: 1, fadeIn: 0.3, fadeOut: 0.3, data: data.map((d) => ({ idx: av.P.index[d[0]], value: d[1], blend: d[2] })), until: 0 }; };
  return { av, run, seen, expr, cm, vals };
}

const out = {};
for (const [label, hooked] of [['fixed', true], ['unfixed', false]]) {
  const m = model(hooked), o = {};
  m.expr('wings', [['WingFlap', 1, 'Overwrite'], ['ToeTap', 8, 'Add']]);
  o.rest = m.run(0.5);
  o.on = m.run(1);                                          // faded in: the values are the expression's
  m.av.exprs.wings.to = 0;
  o.off = m.run(1.5);                                       // faded out and gone
  o.gone = !('wings' in m.av.exprs);
  o.later = m.run(1);
  // an Add expression stays at its value however long it is on
  const a = model(hooked);
  a.expr('big', [['ArmWiggle', 5, 'Add']]);
  a.run(0.5);
  o.addLong = a.run(4).ArmWiggle;
  // a held API value on a parameter nothing else drives, then released
  const h = model(hooked);
  h.av.command({ cmd: 'params', values: { ArmWiggle: 20 }, fade: 0, hold: true });
  o.held = h.run(1).ArmWiggle;
  h.av.command({ cmd: 'release', ids: ['ArmWiggle'], fade: 0.2 });
  o.released = h.run(1.5).ArmWiggle;
  // the same on the final layer was never a problem; and a parameter a motion animates keeps the motion's value
  const f = model(hooked);
  f.av.command({ cmd: 'params', values: { ArmWiggle: 12 }, fade: 0, hold: true, layer: 'final' });
  o.finalHeld = f.run(1).ArmWiggle;
  f.av.command({ cmd: 'release', ids: ['ArmWiggle'], fade: 0.2 });
  o.finalReleased = f.run(1.5).ArmWiggle;
  const mo = model(hooked);
  mo.expr('wiggle', [['ArmWiggle', 4, 'Add']]);
  o.overMotion = mo.run(1, { ArmWiggle: 10 }).ArmWiggle;    // the motion's 10, plus the expression's 4
  mo.av.exprs.wiggle.to = 0;
  o.motionAlone = mo.run(1.5, { ArmWiggle: 10 }).ArmWiggle;
  out[label] = o;
}
console.log(JSON.stringify(out));
