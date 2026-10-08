/* Test helper (tests/test_avatar.py): where things glued to an avatar end up. Runs the real avatar_engine.js under node with a
 * fake parent model (a triangle that can be turned, moved and mirrored) and prints the numbers as JSON:
 *   node avatar_engine_mount.js <path to avatar_engine.js>
 * Covers an avatar hanging from another one's pin point, the update order, items placed by pin point name, and a soundboard clip. */
'use strict';
const { load } = require('./avatar_engine_harness.js');

const file = process.argv[2];
const W = 1920, H = 1080;
class Point { constructor(x, y) { this.x = x; this.y = y; } }
const { HA } = load(file, { PIXI: { Point } });

// a display object with real numbers: local -> root and back, as Pixi does it
function fakeNode(x, y, rot, sx, sy) {
  const n = { position: { x, y, set(a, b) { this.x = a; this.y = b; } }, scale: { x: sx, y: sy, set(a, b) { this.x = a; this.y = b; } },
              rotation: rot || 0, zIndex: 0, visible: true, alpha: 1, filters: null };
  n.toGlobal = (p) => { const c = Math.cos(n.rotation), s = Math.sin(n.rotation), px = p.x * n.scale.x, py = p.y * n.scale.y;
                        return new Point(n.position.x + c * px - s * py, n.position.y + s * px + c * py); };
  n.toLocal = (g) => { const dx = g.x - n.position.x, dy = g.y - n.position.y, c = Math.cos(-n.rotation), s = Math.sin(-n.rotation);
                       return new Point((c * dx - s * dy) / n.scale.x, (s * dx + c * dy) / n.scale.y); };
  return n;
}

// a model whose one art mesh, ArtHead, is the triangle `v` (model space = the avatar's node space)
function fakeModel(node, v) {
  return { internalModel: { getDrawableIndex: (id) => (id === 'ArtHead' ? 0 : -1), getDrawableVertices: () => v },
           toGlobal: node.toGlobal, toLocal: node.toLocal };
}

const TRI = [0, 0, 100, 0, 0, 100];                                         // (0,0) (100,0) (0,100)
const PIN = { mesh: 'ArtHead', tri: [0, 1, 2], bary: [1 / 3, 1 / 3, 1 / 3], angle0: 0, follow_angle: true };    // its middle: (33.3, 33.3)

function stageWith(defs) {
  const stage = new HA.Stage({ mount: {} });
  stage.root = { addChild() {} };
  stage.models = {};
  for (const d of defs) {
    const av = new HA.Avatar(stage, Object.assign({ name: d.name, x: 50, y: 50, scale: 1, rotation: 0, flip: false, visible: true, idle: {}, mouth: { lipsync: false } }, d.cfg || {}));
    av.node = fakeNode(d.x == null ? 960 : d.x, d.y == null ? 540 : d.y, d.rot || 0, d.sx == null ? 1 : d.sx, d.sy == null ? 1 : d.sy);
    av.boxWidth = () => d.box || 600;
    if (d.verts) av.model = fakeModel(av.node, d.verts);
    stage.avatars[d.name] = av;
  }
  stage.order = defs.map((d) => d.name);
  stage.config = { avatars: defs.map((d) => stage.avatars[d.name].cfg) };
  return stage;
}

const out = {};
const near = (n) => (n == null ? n : Math.round(n * 1000) / 1000);
const pose = (av) => ({ x: near(av.node.position.x), y: near(av.node.position.y), rot: near(av.node.rotation), sx: near(av.node.scale.x), sy: near(av.node.scale.y) });
const det = (av) => av.node.scale.x * av.node.scale.y;
const at = (extra) => Object.assign({ to: 'hex', anchor: '', pin: PIN, x: 50, y: 20, dx: 0, dy: 0, layer: 'front', follow_angle: true, mirror: false }, extra || {});

function pair(parentOpts, attach, childScale) {
  const s = stageWith([Object.assign({ name: 'hex', verts: TRI }, parentOpts || {}), { name: 'mini', cfg: { attach: attach } }]);
  const child = s.avatars.mini;
  child.tr.cur.scale = childScale == null ? 0.5 : childScale;
  s._mounts();
  child._placeNode();
  return { s, parent: s.avatars.hex, child };
}

{ // glued to the middle of the parent's triangle: it sits there, at its own scale
  const { child } = pair({}, at());
  out.onPin = pose(child);
}
{ // the head turns a quarter: the point goes round with it and the child turns too
  const { child } = pair({ verts: [0, 0, 0, 100, -100, 0] }, at());
  out.turned = pose(child);
}
{ // follow_angle off keeps it upright on the moving point
  const { child } = pair({ verts: [0, 0, 0, 100, -100, 0] }, at({ follow_angle: false }));
  out.upright = pose(child);
}
{ // the parent is moved, scaled and tilted as a whole: the child is carried along (2x, 90 degrees)
  const { child } = pair({ x: 100, y: 200, rot: Math.PI / 2, sx: 2, sy: 2 }, at());
  out.carried = pose(child);
}
{ // the child's offset is in its own size (a quarter of its 600 wide box, 50 % of 1080 up), scaled with it
  const { child } = pair({}, at({ dx: 25, dy: -50 }));
  out.nudged = pose(child);
}
{ // a flipped parent mirrors the pin point, but the child's art is turned back (mirror: false) ...
  const { child } = pair({ sx: -1 }, at());
  out.flippedParent = Object.assign(pose(child), { mirrored: det(child) < 0 });
  const m = pair({ sx: -1 }, at({ mirror: true }));       // ... or mirrored with it
  out.flippedParentMirror = { mirrored: det(m.child) < 0 };
  const own = pair({}, at());                              // the child's own flip mirrors it
  own.child.tr.flip = true; own.child._placeNode();
  out.ownFlip = { mirrored: det(own.child) < 0, x: near(own.child.node.position.x) };
}
{ // the pin's mesh is not on the parent (another model): the free spot x / y of the parent's box; no parent: its own place
  const { child } = pair({}, at({ pin: Object.assign({}, PIN, { mesh: 'Gone' }), x: 60, y: 30 }));
  out.freeSpot = pose(child);
  const s = stageWith([{ name: 'mini', cfg: { attach: at({ to: 'ghost' }), x: 25, y: 75 } }]);
  s.avatars.mini._tickTransform(0);
  out.noParent = { x: near(s.avatars.mini.node.position.x), y: near(s.avatars.mini.node.position.y), parent: s.avatars.mini.mountParent };
}
{ // a pin point of the model, by name (any case), beats the raw pin
  const s = stageWith([{ name: 'hex', verts: TRI, cfg: { model: 'm' } }]);
  s.models = { m: { anchors: { 'Head Top': Object.assign({}, PIN, { bary: [1, 0, 0] }), Hand: PIN } } };
  const hex = s.avatars.hex; hex.modelId = 'm';
  out.anchor = { exact: hex.attachFrame({ anchor: 'Head Top', pin: PIN, x: 50, y: 50 }), anyCase: hex.attachFrame({ anchor: 'hand', x: 50, y: 50 }).x,
                 unknown: hex.attachFrame({ anchor: 'tail', pin: PIN, x: 50, y: 50 }).x, none: hex.anchorPin('tail'), freeBox: hex.attachFrame({ anchor: '', x: 100, y: 0 }) };
}
{ // who is updated before whom, what is drawn over whom, a loop and an avatar hanging from nothing
  const s = stageWith([{ name: 'c', cfg: { attach: at({ to: 'b' }) } }, { name: 'b', cfg: { attach: at({ to: 'a', layer: 'back' }) } }, { name: 'a' }, { name: 'free' },
                       { name: 'x', cfg: { attach: at({ to: 'y' }) } }, { name: 'y', cfg: { attach: at({ to: 'x' }) } }, { name: 'lost', cfg: { attach: at({ to: 'ghost' }) } }]);
  s.order.forEach((n, i) => { s.avatars[n].node.zIndex = i; });
  s._mounts();
  const z = (n) => near(s.avatars[n].node.zIndex);
  out.mounts = { update: s.updateOrder, parents: s.order.map((n) => (s.avatars[n].mountParent ? s.avatars[n].mountParent.name : null)),
                 z: { a: z('a'), b: z('b'), c: z('c') }, draw: s.drawOrder };
}
{ // letting go keeps the place: the numbers that would put it where it stands
  const { child } = pair({ x: 100, y: 200, rot: Math.PI / 2, sx: 2, sy: 2 }, at());
  out.placed = child.worldPlacement();
  const f = pair({ sx: -1 }, at());
  out.placedBesideFlipped = f.child.worldPlacement();
  const m = pair({ sx: -1 }, at({ mirror: true }));
  out.placedMirrored = m.child.worldPlacement();
}
{ // an item by pin point name rides it; without one it is at its % of the box
  const s = stageWith([{ name: 'hex', verts: [0, 0, 0, 100, -100, 0] }]);
  s.models = { m: { anchors: { Top: PIN } } };
  const hex = s.avatars.hex; hex.modelId = 'm';
  const item = { av: hex, node: fakeNode(0, 0, 0, 1, 1), cfg: { anchor: 'Top', pin: null, x: 50, y: 30, rotation: 90 } };
  HA.Item.prototype._place.call(item);
  out.item = { x: near(item.node.position.x), y: near(item.node.position.y), rot: near(item.node.rotation) };
  item.cfg = { anchor: '', pin: null, x: 60, y: 50, rotation: 0 };
  HA.Item.prototype._place.call(item);
  out.itemFree = { x: near(item.node.position.x), y: near(item.node.position.y), rot: near(item.node.rotation) };
}
{ // a soundboard clip glued to the pin point: where the page puts it (the stage is fitted at half size, 10 px in)
  const s = stageWith([{ name: 'hex', verts: TRI }]);
  s.scale = 0.5; s.root = { position: { x: 10, y: 20 } };
  const visual = { style: {} };
  s.clips = [{ visual, avatar: 'hex', at: { avatar: 'hex', anchor: '', pin: PIN, x: 50, y: 20, dx: 5, dy: -10, scale: 2, rotation: 15, follow_angle: true, mirror: false } }];
  HA.Stage.prototype._clipsTick.call(s);
  const m = /^matrix\(([^)]*)\) (.*)$/.exec(visual.style.transform);
  out.clip = { matrix: m[1].split(',').map(Number), rest: m[2], shown: visual.style.visibility };
  s.avatars.hex.model = null;                                      // not loaded (yet): hidden
  HA.Stage.prototype._clipsTick.call(s);
  out.clipHidden = visual.style.visibility;
  s.avatars.hex.model = fakeModel(s.avatars.hex.node, TRI);
  s.avatars.hex.node.scale.x = -1;                                 // a flipped avatar: the clip is turned back unless it mirrors
  HA.Stage.prototype._clipsTick.call(s);
  out.clipFlipped = visual.style.transform.indexOf('scaleX(-1)') >= 0;
}
{ // anything glued to an avatar wants its pose updated at the start of the frame (not when it is drawn), once
  const s = stageWith([{ name: 'hex', verts: TRI }, { name: 'mini', cfg: { attach: at() } }, { name: 'lone' }]);
  const calls = [], hex = s.avatars.hex, lone = s.avatars.lone;
  hex.model.deltaTime = 16; hex.model.elapsedTime = 500; hex.model.internalModel.update = (dt, e) => calls.push([dt, e]);
  const want = () => ({ hex: hex._wantsFreshPose(), lone: lone._wantsFreshPose() });
  out.poseBeforeMounts = want();
  s._mounts();
  out.poseHung = want();                                               // mini hangs from hex
  s.avatars.mini.cfg = Object.assign({}, s.avatars.mini.cfg, { attach: null }); s._mounts();
  out.poseLetGo = want();
  lone.items = [{ cfg: { pin: null, anchor: '' } }]; out.poseFreeItem = lone._wantsFreshPose();
  lone.items = [{ cfg: { pin: PIN } }]; out.posePinnedItem = lone._wantsFreshPose();
  lone.items = [{ cfg: { anchor: 'Top' } }]; out.poseAnchoredItem = lone._wantsFreshPose();
  lone.items = []; s.clips = [{ avatar: 'lone' }]; out.poseClip = lone._wantsFreshPose();
  s.clips = []; out.poseNothing = lone._wantsFreshPose();
  hex._poseNow(); hex._poseNow();                                      // the second finds nothing pending
  out.poseCalls = calls; out.poseDeltaAfter = hex.model.deltaTime;
}
console.log(JSON.stringify(out));
