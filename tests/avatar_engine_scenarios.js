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
{ // a PNGtuber's mouth follows the voice (open and shut with the words) and it bounces as the audio comes in
  const { HA } = load(file);
  // `voice(t)` is the level the lipsync analysis sees (0..1, before its smoothing): ~22 ms of analysis window, then the engine's frame
  const run = (rigExtra, voice, seconds) => {
    const av = { inputOv: (n, d) => d, cfg: { idle: { speech_motion: 50 } }, baseScale: 1, partOv: null };
    const spec = Object.assign({ style: 'simple', states: { neutral: { idle: 'i.png', talk: 't.png' } }, default_state: 'neutral', bounce: 250, beat: 120,
                                 gravity: 1000, threshold: 0.12, hold: 0.22, snap: 0.6, mouth: 'follow', breathe: false }, rigExtra);
    const rig = new HA.PngRig(av, spec); rig.box = { x: 0, y: 0, w: 300, h: 300 };
    const dt = 1 / 60;
    let raw = 0, opens = 0, openFrames = 0, kicks = 0, prevVy = 0, was = false, stuckOpenAfter = false, n = 0, maxBy = 0;
    for (let i = 0; i < Math.round((seconds || 6) * 60); i++) {
      const t = i * dt;
      raw += (voice(t) - raw) * (1 - Math.exp(-dt / 0.022));
      rig.update(dt, { lip: { level: raw, raw: raw }, MouthOpen: 0 }, t);
      n++;
      if (rig.talking && !was) opens++;
      if (rig.talking) openFrames++;
      was = rig.talking;
      if (rig.vy < -1 && prevVy >= -1) kicks++;
      prevVy = rig.vy;
      maxBy = Math.min(maxBy, rig.by);
      if (t > 5.2 && rig.talking) stuckOpenAfter = true;               // the voice ended at 4.3 s
    }
    return { opens, openPct: Math.round(100 * openFrames / n), kicks, maxBy: +maxBy.toFixed(1), stuckOpenAfter };
  };
  // 4 s of speech at 5 syllables a second: each peaks at .85 and dips to .4, with a gap after every fourth
  const speech = (t) => {
    if (t < 0.3 || t > 4.3) return 0;
    const u = ((t - 0.3) * 5) % 1;
    return Math.floor((t - 0.3) * 5) % 4 === 3 && u > 0.55 ? 0 : 0.4 + 0.45 * Math.sin(Math.PI * Math.min(1, u * 1.15)) ** 2;
  };
  out.pngFollow = run({}, speech);
  out.pngFollowNoBeat = run({ beat: 0 }, speech);
  out.pngHold = run({ mouth: 'hold' }, speech);
  out.pngSteady = run({}, (t) => (t > 0.3 && t < 3 ? 0.7 : 0));
  out.pngLazy = run({ snap: 0 }, speech);
  out.pngSnappy = run({ snap: 1 }, speech);
  out.pngNoBounce = run({ bounce: 0, beat: 0 }, speech);
  out.pngSilent = run({}, () => 0);
  out.pngLoudLevel = run({}, (t) => (t > 0.3 && t < 4.3 ? 0.05 : 0)).opens;      // under the threshold: never opens
  const gate = new HA.MouthGate();                                                 // the gate by itself: a steady voice keeps it open, silence shuts it
  let shut = 0;
  for (let i = 0; i < 120; i++) { gate.update(0.6, 1 / 60, { threshold: 0.12, snap: 0.6, minOpen: 0.05, minClosed: 0.045 }); }
  const openSteady = gate.open;
  for (let i = 0; i < 20; i++) { if (!gate.update(0, 1 / 60, { threshold: 0.12, snap: 0.6, minOpen: 0.05, minClosed: 0.045 })) shut++; }
  out.pngGate = { openSteady, shutWithinThirdOfASecond: shut > 0 };
}
{ // colours: multiply / overlay / alpha on the whole model, parts (folders) and single art meshes, in layers
  const { HA, env } = load(file);
  // parts: Body (a folder) > Hair > Fringe, and Eyes; art meshes: 0 ArtBody in Body, 1 ArtHair in Hair, 2 ArtFringe in Fringe, 3 ArtEye in Eyes, 4 ArtLoose in no part
  const parts = [{ id: 'Body', up: -1 }, { id: 'Hair', up: 0 }, { id: 'Fringe', up: 1 }, { id: 'Eyes', up: -1 }];
  const owner = [0, 1, 2, 3, -1], dids = ['ArtBody', 'ArtHair', 'ArtFringe', 'ArtEye', 'ArtLoose'];
  const rgba = (r, g, b, a) => ({ r, g, b, a: a === undefined ? 1 : a });
  const fakeCm = () => {
    const nm = owner.map((_, i) => (i === 3 ? rgba(0.5, 0.5, 0.5) : rgba(1, 1, 1)));       // the eye mesh has a grey multiply colour of its own ...
    const ns = owner.map((_, i) => (i === 3 ? rgba(0.2, 0.2, 0.2) : rgba(0, 0, 0)));       // ... and a screen colour
    const um = owner.map(() => rgba(1, 1, 1)), us = owner.map(() => rgba(0, 0, 0)), fm = owner.map(() => false), fs = owner.map(() => false);
    const ops = new Float32Array(owner.length).fill(1);
    const r3 = (c) => [c.r, c.g, c.b].map((v) => +v.toFixed(3));
    return { fm, fs, ops, _model: { drawables: { opacities: ops } },
      getDrawableCount: () => owner.length, getPartCount: () => parts.length, getDrawableParentPartIndex: (d) => owner[d],
      getDrawableId: (d) => ({ getString: () => ({ s: dids[d] }) }),
      getPartParentPartIndices: () => Int32Array.from(parts.map((p) => p.up)),
      getDrawableMultiplyColor: (d) => Object.assign({}, nm[d]), getDrawableScreenColor: (d) => Object.assign({}, ns[d]),
      setMultiplyColorByRGBA: (d, r, g, b, a) => { um[d] = rgba(r, g, b, a); }, setScreenColorByRGBA: (d, r, g, b, a) => { us[d] = rgba(r, g, b, a); },
      setOverrideFlagForDrawableMultiplyColors: (d, f) => { fm[d] = f; }, setOverrideFlagForDrawableScreenColors: (d, f) => { fs[d] = f; },
      refresh: () => ops.fill(1),                                      // what the model's own update does to the opacities each frame
      multiply: (d) => r3(fm[d] ? um[d] : nm[d]), overlay: (d) => r3(fs[d] ? us[d] : ns[d]), alpha: (d) => +ops[d].toFixed(3) };
  };
  const make = (colors) => {
    const av = bare(HA);
    av.cfg = Object.assign({}, av.cfg, { colors: colors || {} });
    av._readColors(av.cfg, true);
    const cm = fakeCm();
    av.model = { internalModel: { coreModel: cm } };
    av.Parts = { ids: parts.map((p) => p.id), index: Object.create(null) };
    return { av, cm };
  };
  const run = (t, seconds) => { for (let i = 0; i < Math.round(seconds * 60); i++) { env.clock += 1 / 60; t.av._colorTick(1 / 60, env.clock); t.cm.refresh(); t.av._applyColors(); } };
  const all = (t, f) => [0, 1, 2, 3, 4].map((d) => t.cm[f](d));
  const send = (t, c) => t.av.command(Object.assign({ fade: 0 }, c));

  let t = make({ parts: { Hair: { multiply: '#ff8000' } } }); run(t, 0.1);
  out.mulHair = all(t, 'multiply');                                      // Hair and the Fringe inside it - not the Body around it
  out.mulHairFlags = t.cm.fm.slice();
  t = make({ all: { multiply: '#808080' } }); run(t, 0.1);
  out.mulWhole = all(t, 'multiply');                                     // every mesh, the part-less one too; the eye's own grey (.5) stays in
  t = make({ parts: { Hair: { multiply: '#ff0000' } }, all: { multiply: '#808080' } }); run(t, 0.1);
  out.mulBoth = all(t, 'multiply')[1];                                   // a part's multiply x the whole model's
  t = make({ parts: { Body: { multiply: '#0000ff' }, Hair: { multiply: '#ff0000' } } }); run(t, 0.1);
  out.mulFolders = all(t, 'multiply').slice(0, 3);                       // Body blue: the hair inside it is blue x red
  t = make({ meshes: { ArtFringe: { multiply: '#00ff00' } }, parts: { Hair: { multiply: '#ff8000' } } }); run(t, 0.1);
  out.mulMesh = all(t, 'multiply').slice(0, 3);                          // one mesh: its own, on top of its part's (orange x green)

  t = make({ parts: { Hair: { overlay: '#404040' } }, meshes: { ArtEye: { overlay: '#808080' } } }); run(t, 0.1);
  out.overlay = all(t, 'overlay');                                       // lightens; the eye mesh's own screen colour (.2) screens with the new one
  out.overlayLeavesMultiply = all(t, 'multiply');
  t = make({ parts: { Hair: { overlay: '#808080' }, Fringe: { overlay: '#808080' } } }); run(t, 0.1);
  out.overlayStacks = all(t, 'overlay')[2];                              // two screens: 1 - (1 - .5)(1 - .5) = .75

  t = make({ meshes: { ArtFringe: { alpha: 0 } } }); run(t, 0.1);
  out.alphaMesh = all(t, 'alpha');                                       // invisible: one mesh, nothing else
  t = make({ parts: { Hair: { alpha: 0.5 } }, meshes: { ArtFringe: { alpha: 0.5 } } }); run(t, 0.1);
  out.alphaStacks = all(t, 'alpha'); run(t, 0.5);
  out.alphaDoesNotCompound = all(t, 'alpha');                            // the model refreshes its opacities every frame: the same numbers, not .5 x .5 x ...
  t = make({ all: { alpha: 0.2 } }); run(t, 0.1);
  out.alphaWhole = all(t, 'alpha');
  // the model does not rewrite an opacity on an update where nothing changed (a still model): the alpha must not shrink frame after frame
  const runStatic = (tt, seconds) => { for (let i = 0; i < Math.round(seconds * 60); i++) { env.clock += 1 / 60; tt.av._colorTick(1 / 60, env.clock); tt.av._applyColors(); } };
  t = make({ parts: { Hair: { alpha: 0.5 } } }); runStatic(t, 0.5);
  out.alphaStillModel = all(t, 'alpha');
  t.cm.ops[1] = 0.8;                                                        // the model's own animation sets this mesh to .8 (it rewrote the value)
  runStatic(t, 0.1); out.alphaOverTheModelsValue = t.cm.alpha(1);
  t.av.cfg = Object.assign({}, t.av.cfg, { colors: {} }); t.av._readColors(t.av.cfg, false); runStatic(t, 1);
  out.alphaHandedBack = all(t, 'alpha');                                    // the model's value is back (.8), not ours

  t = make(); run(t, 0.2);
  out.nothingTouchesNothing = t.cm.fm.every((f) => !f) && t.cm.fs.every((f) => !f) && t.av._tintedN === 0;
  send(t, { cmd: 'colors', colors: { parts: { Hair: { multiply: '#000000' } } }, fade: 0.6 });   // a bot's colour eases in over `fade` ...
  run(t, 1 / 60); out.easeFirst = t.cm.multiply(1)[0];
  run(t, 0.25); out.easeMid = t.cm.multiply(1)[0];
  run(t, 1.5); out.easeEnd = t.cm.multiply(1)[0];
  send(t, { cmd: 'release_colors', parts: ['Hair'], fade: 0.3 });            // ... and lets go the same way, handing the mesh back
  run(t, 0.2); out.releaseMid = t.cm.multiply(1)[0];
  run(t, 1.5); out.releaseEnd = t.cm.multiply(1)[0];
  out.releaseHandsBack = t.cm.fm.every((f) => !f) && t.cm.fs.every((f) => !f) && t.av._tintedN === 0 && !t.av.colOn;
  send(t, { cmd: 'colors', colors: { meshes: { ArtHair: { alpha: 0 } } } }); run(t, 0.2); out.heldAlpha = t.cm.alpha(1);
  send(t, { cmd: 'release_colors', everything: true }); run(t, 0.2); out.heldAlphaReleased = t.cm.alpha(1);      // {} = everything

  // the preview's flash: what is selected in the Colors tab lights up for a moment, a hidden mesh shows through, and never on stream
  t = make({ meshes: { ArtFringe: { alpha: 0 } } }); run(t, 0.1);
  t.av.stage.role = 'obs'; t.av.flash('parts', 'Hair'); out.flashIgnoredOnStream = t.av.hl === null;
  t.av.stage.role = 'preview'; t.av.flash('parts', 'Hair', 1.6);
  let hairLit = 0, ghost = 0, bodyLit = 0, flashFrames = 0;
  for (let i = 0; i < 110; i++) {
    run(t, 1 / 60);
    if (t.av.hl) flashFrames++;
    hairLit = Math.max(hairLit, t.cm.overlay(1)[0]); ghost = Math.max(ghost, t.cm.alpha(2)); bodyLit = Math.max(bodyLit, t.cm.overlay(0)[0], t.cm.alpha(0) < 1 ? 1 : 0);
  }
  out.flash = { hairLit: hairLit > 0.4, ghostShowsThrough: ghost > 0.4, bodyUntouched: bodyLit === 0, ended: t.av.hl === null && flashFrames > 90 && flashFrames < 105 };
  out.flashLeavesNothingBehind = [t.cm.overlay(1), t.cm.alpha(2), t.cm.alpha(1)];                // the hidden mesh is hidden again, the hair is as it was
  t.av.flash('meshes', 'ArtEye', 0.5); run(t, 0.1); out.flashOneMesh = [t.cm.overlay(3)[0] > 0.2, t.cm.overlay(1)[0] === 0];
  run(t, 0.6); out.flashOneMeshEnded = t.cm.overlay(3);                                          // (the eye's own screen colour is all that is left)

  // layers: the saved colours, then the presets in the order they went on, then what a bot holds - a later one wins, field by field
  t = make({ parts: { Hair: { multiply: '#00ff00' } } }); run(t, 0.1);
  const blue = { parts: { Hair: { multiply: '#0000ff' } } }, red = { parts: { Hair: { multiply: '#ff0000', overlay: '#202020' } } };
  send(t, { cmd: 'color_preset', states: { Blue: 'on' }, rules: { Blue: blue }, orders: { Blue: 1 } }); run(t, 0.2); out.layerBlue = t.cm.multiply(1);
  send(t, { cmd: 'color_preset', states: { Red: 'on' }, rules: { Red: red }, orders: { Red: 2 } }); run(t, 0.2);
  out.layerRed = t.cm.multiply(1); out.layerRedOverlay = t.cm.overlay(1);        // the later preset wins ...
  send(t, { cmd: 'color_preset', states: { Red: 'off' } }); run(t, 0.2);
  out.layerRedOff = [t.cm.multiply(1), t.cm.overlay(1)];                      // ... and the one under it is back (its fields only: no overlay)
  send(t, { cmd: 'color_preset', states: { Blue: 'off' } }); run(t, 0.2); out.layerSaved = t.cm.multiply(1);
  send(t, { cmd: 'color_preset', states: { Red: 'on' }, rules: { Red: red }, orders: { Red: 2 } });
  send(t, { cmd: 'colors', colors: { parts: { Hair: { multiply: '#ffff00' } } } }); run(t, 0.2); out.heldWinsOverPreset = [t.cm.multiply(1), t.cm.overlay(1)];   // the held multiply, the preset's overlay
  send(t, { cmd: 'colors', colors: { parts: { Hair: { multiply: null } } } }); run(t, 0.2); out.nullGoesBack = t.cm.multiply(1);      // a field set to null goes back to the layer below
  send(t, { cmd: 'clear_color_presets' }); run(t, 0.2); out.clearedPresets = [t.cm.multiply(1), t.cm.overlay(1)];
  send(t, { cmd: 'colors', colors: { parts: { Hair: { multiply: '#ff0000' } } }, until: env.clock + 1 }); run(t, 0.5); out.untilHolds = t.cm.multiply(1);
  run(t, 1); out.untilEnds = t.cm.multiply(1);
  send(t, { cmd: 'color_preset', states: { Red: 'on' }, rules: { Red: red }, orders: { Red: 2 }, until: env.clock + 1 }); run(t, 0.5); out.presetUntilHolds = t.cm.multiply(1);
  run(t, 1); out.presetUntilEnds = t.cm.multiply(1);

  const late = make();                                                    // a renderer that joins late starts in the live state, no fade
  late.av.restore({ colors: { 'parts:Hair': { kind: 'parts', id: 'Hair', spec: { multiply: '#ff0000' }, until: null } },
                    color_presets: { Blue: { rules: { meshes: { ArtEye: { alpha: 0.5 } } }, order: 3, until: null } } });
  late.av._colorTick(1 / 60, env.clock); late.cm.refresh(); late.av._applyColors();
  out.restoredAtOnce = [late.cm.multiply(1), late.cm.alpha(3)];

  const saved = make({ parts: { Hair: { multiply: '#ff0000' } } }); run(saved, 0.1);   // the saved colours change (the colour pickers): the avatar follows
  saved.av.cfg = Object.assign({}, saved.av.cfg, { colors: {} }); saved.av._readColors(saved.av.cfg, false); run(saved, 1);
  out.savedCleared = saved.cm.fm.every((f) => !f) && saved.av._tintedN === 0;

  const old = make({ parts: { Hair: { multiply: '#ff0000' } } }); delete old.cm.setScreenColorByRGBA;   // a runtime without Cubism 5's colour overrides: nothing happens, no error
  run(old, 0.1); out.oldRuntimeIsHarmless = old.cm.fm.every((f) => !f);

  const reload = make({ parts: { Hair: { multiply: '#ff0000' } } }); run(reload, 0.1);          // a new model: the old one's meshes are forgotten
  reload.av._colTbl = null; reload.av._tinted = {}; reload.av._tintedN = 0;
  const cm2 = fakeCm(); reload.av.model = { internalModel: { coreModel: cm2 } }; run(reload, 0.1);
  out.reloadColoursTheNewModel = cm2.multiply(1);

  const lone = bare(HA);                                                  // a PNGtuber layer: multiply and alpha (no overlay), its own x `all`
  lone.cfg = Object.assign({}, lone.cfg, { colors: { all: { multiply: '#808080', alpha: 0.5 }, parts: { a: { multiply: '#ff8000', alpha: 0.5, overlay: '#ffffff' } } } });
  lone._readColors(lone.cfg, true);
  out.pngLayer = [lone.pngTint('a'), lone.pngTint('b'), bare(HA).pngTint('a'), lone.pngAlpha('a'), lone.pngAlpha('b'), bare(HA).pngAlpha('a')];
}
console.log(JSON.stringify(out));
