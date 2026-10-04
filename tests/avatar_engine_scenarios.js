/* Test helper (tests/test_avatar.py): what the Avatars renderer does with `params` keys. Prints the numbers as JSON:
 *   node avatar_engine_scenarios.js <path to avatar_engine.js>
 * Mapping inputs named My... are made up: any model's .vtube.json can name custom inputs (VTube Studio's custom
 * parameters), and a bot streams values for them with `params`. */
'use strict';
const { rig, bare, attach, load, P, row, STD } = require('./avatar_engine_harness.js');

const file = process.argv[2];

// a model whose mappings take custom inputs - and one input that is also a parameter id (ParamBodyAngleX, MyTap)
const CUSTOM = {
  params: [P('ParamAngleX', -30, 30, 0), P('ParamAngleZ', -30, 30, 0), P('ParamBodyAngleX', -30, 30, 0),
           P('ParamEyeLOpen', 0, 1.3, 1), P('ParamEyeROpen', 0, 1.3, 1), P('ParamMouthOpenY', 0, 1, 0.5), P('MyTap', -10, 10, 0)],
  mappings: [row('MyHeadX', 'ParamAngleX', -30, 30, -30, 30, 20),
             row('MyEyeOpenL', 'ParamEyeLOpen', 0, 1.3, 0, 1.3, 0, { blink: true }),
             row('MyEyeOpenR', 'ParamEyeROpen', 0, 1.3, 0, 1.3, 0, { blink: true }),
             row('MyMouthOpen', 'ParamMouthOpenY', 0, 1, 0, 1, 0),
             row('ParamBodyAngleX', 'ParamBodyAngleX', -30, 30, -10, 10, 0),
             row('MyTap', 'MyTap', -1, 1, -10, 10, 0)],
};

const out = {};
const frames = (r, seconds) => { for (let i = 0; i < Math.round(seconds * 60); i++) r.step(); };

{ // a custom input eases to its value, and lets go after `for`
  const r = rig(file, CUSTOM, { blink: false });
  frames(r, 0.5);
  out.rest = r.val('ParamAngleX');
  r.av.command({ cmd: 'params', values: { MyHeadX: 28 }, until: r.env.clock + 3 });
  r.step(); out.firstFrame = r.val('ParamAngleX');
  frames(r, 0.5); out.after05 = r.val('ParamAngleX');
  frames(r, 2.2); out.after27 = r.val('ParamAngleX');
  frames(r, 0.5); out.after32 = r.val('ParamAngleX');
  frames(r, 1.0); out.after42 = r.val('ParamAngleX');
}
{ // a custom eye input, a mapping that wins over a parameter of the same id, a raw parameter
  const r = rig(file, CUSTOM, { blink: false });
  r.av.command({ cmd: 'params', values: { MyEyeOpenL: 0.5, ParamBodyAngleX: 30, ParamAngleZ: 10, MyTap: 1 } });
  frames(r, 1);
  out.eyeL = r.val('ParamEyeLOpen'); out.eyeRUntouched = r.val('ParamEyeROpen');
  out.bodyAngle = r.val('ParamBodyAngleX'); out.angleZ = r.val('ParamAngleZ'); out.toe = r.val('MyTap');
  out.kinds = ['MyEyeOpenL', 'ParamBodyAngleX', 'ParamAngleZ', 'MyTap'].map((k) => r.av.ov[k].isInput);
}
{ // held values replayed before the model has loaded still land as inputs; another model makes them plain keys again
  const { HA, env } = load(file);
  const av = bare(HA, { blink: false });
  const held = (k, v) => ({ values: { [k]: v }, mode: 'set', layer: 'input', weight: 1, duration: 0, until: null, fade: 0.3 });
  av.restore({ params: { MyHeadX: held('MyHeadX', 20), ParamAngleZ: held('ParamAngleZ', 7) } });
  out.beforeModel = ['MyHeadX', 'ParamAngleZ'].map((k) => av.ov[k].isInput);
  const m = attach(av, CUSTOM);
  out.afterModel = ['MyHeadX', 'ParamAngleZ'].map((k) => av.ov[k].isInput);
  for (let i = 0; i < 120; i++) { env.clock += 1 / 60; av._drive(1 / 60, env.clock); av._inputLayer(m.cm); av._finalLayer(m.cm); }
  out.restoredHead = m.vals[av.P.index.ParamAngleX]; out.restoredZ = m.vals[av.P.index.ParamAngleZ];
  av.mset = { mappings: [] }; av._compile();
  out.afterOtherModel = ['MyHeadX', 'ParamAngleZ'].map((k) => av.ov[k].isInput);
}
{ // blinking on a custom eye input held open (and not on a row that is not marked for it)
  const dips = (idle, input, param) => {
    const r = rig(file, CUSTOM, idle);
    r.av.command({ cmd: 'params', values: { [input]: 1 } });
    frames(r, 0.5);
    let min = 9, max = -9, n = 0, below = false;
    for (let i = 0; i < 60 * 30; i++) {
      r.step();
      const v = r.val(param);
      min = Math.min(min, v); max = Math.max(max, v);
      if (v < 0.3 && !below) { n++; below = true; } else if (v > 0.8) below = false;
    }
    return { min: +min.toFixed(3), max: +max.toFixed(3), dips: n };
  };
  out.blinkOn = dips({}, 'MyEyeOpenL', 'ParamEyeLOpen');
  out.blinkOff = dips({ blink: false }, 'MyEyeOpenL', 'ParamEyeLOpen');
  out.notMarked = dips({}, 'MyMouthOpen', 'ParamMouthOpenY');
}
{ // release lets a custom input go
  const r = rig(file, CUSTOM, { blink: false });
  r.av.command({ cmd: 'params', values: { MyHeadX: 20 } }); frames(r, 1); out.held = r.val('ParamAngleX');
  r.av.command({ cmd: 'release', ids: ['MyHeadX'], fade: 0.3 }); frames(r, 1.5); out.released = r.val('ParamAngleX');
  out.cleared = !('MyHeadX' in r.av.ov);
}
{ // keys that are nobody's parameter (and a few that Object.prototype knows) do no harm
  const r = rig(file, CUSTOM, { blink: false });
  r.av.command({ cmd: 'params', values: { constructor: 1, toString: 2, hasOwnProperty: 4, NoSuchParameter: 5 } });
  frames(r, 0.5);
  out.oddKeysFinite = r.vals.every(Number.isFinite);
}
{ // a model with only VTube Studio's standard inputs: nothing changes
  const r = rig(file, STD, { blink: false });
  r.av.command({ cmd: 'params', values: { FaceAngleX: 12, MouthOpen: 0.6 } });
  r.av.command({ cmd: 'params', values: { ParamCheek: 0.7 }, duration: 0.5, ease: 'linear' });
  frames(r, 0.2); out.stdCheekEasing = r.val('ParamCheek');
  frames(r, 1); out.stdAngleX = r.val('ParamAngleX'); out.stdMouth = r.val('ParamMouthOpenY'); out.stdCheek = r.val('ParamCheek');
  out.stdKinds = ['FaceAngleX', 'MouthOpen', 'ParamCheek'].map((k) => r.av.ov[k].isInput);
  const b = rig(file, STD, {});                       // the standard eye rows blink through their input, once
  let min = 9;
  for (let i = 0; i < 60 * 30; i++) { b.step(); min = Math.min(min, b.val('ParamEyeLOpen')); }
  out.stdBlinkMin = +min.toFixed(3);
}
{ // a stage draws the avatars of its own overlay (and of ?avatar=), follows an avatar that is moved, and starts it as it is now
  const { HA } = load(file);
  const stage = (opts) => { const st = new HA.Stage(opts); st.app = { ticker: {} }; st.root = { addChild() {} }; return st; };
  const av = (name, overlay) => Object.assign({ name, x: 50, y: 50, scale: 1, rotation: 0, flip: false, visible: true, idle: {},
                                                mouth: { lipsync: false } }, overlay === undefined ? {} : { overlay });
  const cfg = (list) => ({ avatars: list, overlays: ['main', 'guest'] });
  const names = (st) => Object.keys(st.avatars).sort();
  const main = stage({ overlay: 'main' }), guest = stage({ overlay: 'guest' }), every = stage({}), narrow = stage({ overlay: 'main', only: ['a'] });
  const before = [av('a'), av('b', 'main'), av('c', 'guest')];      // `a` has no overlay: an avatar from before overlays is on main
  [main, guest, every, narrow].forEach((s) => s.setConfig(cfg(before)));
  out.ovMain = names(main); out.ovGuest = names(guest); out.ovEvery = names(every); out.ovNarrow = names(narrow);
  const after = [av('a'), av('b', 'guest'), av('c', 'guest')];      // b is moved to guest
  [main, guest].forEach((s) => s.setConfig(cfg(after)));
  out.ovMovedMain = names(main); out.ovMovedGuest = names(guest);
  const keptA = main.avatars.a;
  const preview = stage({ overlay: 'main' });                       // the tab's preview switches between overlays
  preview.setConfig(cfg(after));
  out.ovPreviewMain = names(preview);
  const outline = { clear() {} }; preview.overlay = outline;        // init() puts the selection outline's graphics here; switching must leave it alone
  preview.setOverlay('guest'); out.ovPreviewGuest = names(preview);
  preview.setOverlay('main'); out.ovPreviewBack = names(preview);
  out.ovLeavesTheSelectionOutline = preview.overlay === outline;
  main.setOverlay('main'); out.ovSameOverlayKeepsAvatars = main.avatars.a === keptA;
  const late = stage({ overlay: 'guest' });                         // an avatar that arrives is in the state the config message carries
  late.setConfig(cfg([av('a'), av('b', 'main')]), {});
  late.setConfig(cfg([av('a'), av('b', 'guest')]),
                 { b: { emotion: { name: 'happy', face: { smile: 1 }, expressions: [], intensity: 1, until: null } } });
  out.ovArrivedEmotion = late.avatars.b.emo.name;
}
console.log(JSON.stringify(out));
