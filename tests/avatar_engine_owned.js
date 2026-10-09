/* Test helper (tests/test_avatar.py): a parameter a playing motion animates (a VTube Studio idle animation, say) while the virtual
 * tracker also maps an input onto it. Runs the real avatar_engine.js under node against a fake Cubism model that does what the
 * engine's update does (motion -> input layer -> save -> final layer -> load). The motion is a slow wave on ParamAngleX that is
 * far from where the tracker's sway sits, so any tracker leaking into the animation shows as a step between frames. Prints JSON:
 *   node avatar_engine_owned.js <path to avatar_engine.js> */
'use strict';
const { load, bare, STD } = require('./avatar_engine_harness.js');

const file = process.argv[2];
const { HA, env } = load(file);
const av = bare(HA, { blink: false });
const n = STD.params.length, vals = new Float32Array(STD.params.map((p) => p.def)), saved = new Float32Array(vals);
const cm = {
  getParameterValueByIndex: (i) => vals[i], setParameterValueByIndex: (i, v) => { vals[i] = v; },
  saveParameters() { saved.set(vals); }, loadParameters() { vals.set(saved); },
};
const state = { currentGroup: 'Idle', currentIndex: 0, currentPriority: 1 };           // an idle animation is playing
av.P = { ids: STD.params.map((p) => p.id), index: Object.create(null), min: STD.params.map((p) => p.min), max: STD.params.map((p) => p.max), def: STD.params.map((p) => p.def) };
STD.params.forEach((p, i) => { av.P.index[p.id] = i; });
av.Parts = { ids: [], index: Object.create(null) };
av.model = { internalModel: { coreModel: cm, motionManager: { state, stopAllMotions() {} } } };
av.kind = 'live2d'; av.mset = { mappings: STD.mappings }; av.partCurves = []; av.restPartOpacity = null;
av._compile();
av._hookLoad(cm);
const X = av.P.index.ParamAngleX;
av.motionKey = 'Idle#0';                                                                 // (what _trackMotion learns from the motion's file)
av.own[X] = { w: 0, to: 1 };

let t = 0;
const wave = () => 18 + 6 * Math.sin(t * 1.3);                                           // the animation's value for this frame
function frame(playing) {
  env.clock += 1 / 60; t += 1 / 60;
  av._drive(1 / 60, env.clock);
  const motion = wave();
  if (playing) cm.setParameterValueByIndex(X, motion);                                   // motions
  av._inputLayer(cm);                                                                    // afterMotionUpdate
  cm.saveParameters();
  av._finalLayer(cm);                                                                    // beforeModelUpdate
  const seen = vals[X];
  cm.loadParameters();
  return [seen, Math.fround(motion)];
}

const out = {};
for (let i = 0; i < 60; i++) frame(true);                                                // the hand-over from the tracker takes 0.3 s
let worst = 0, weights = new Set();
for (let i = 0; i < 300; i++) {                                                          // then the animation owns the parameter: its value, every frame
  const [seen, motion] = frame(true);
  worst = Math.max(worst, Math.abs(seen - motion));
  weights.add(av.own[X].w);
}
out.worstStep = worst;                                                                   // how far the drawn value was from the animation's
out.weights = Array.from(weights);                                                       // the ownership weight, which must not move once it is 1
state.currentGroup = undefined; state.currentIndex = undefined; state.currentPriority = 0;   // the animation ends
for (let i = 0; i < 90; i++) frame(false);
out.releasedEntry = X in av.own;                                                         // and the tracker has the parameter back
out.trackerBack = Math.abs(vals[X]) < 8;                                                 // (its sway, a few degrees - not the animation's 18)
console.log(JSON.stringify(out));
