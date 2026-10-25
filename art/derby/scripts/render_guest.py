"""Render the guest racers: each one running, one stride, seen from the side like the hounds.

    blender -b -P art/derby/scripts/render_guest.py -- <spec.json> <abs path>/art/derby/source/guests <abs path>/art/derby/out/guests [ids]

spec.json: art/derby/guests.json (the add-on's guests) or a local pack's (art/derby/source/guests/local.json: it
never ships). Each guest: model (its folder in source/guests; the first .glb/.gltf/.fbx/.blend in it), height (px tall
standing in the frame), w / h (the frame, px), run (guestlib.RUN overrides: lean, stride, stance, lift, kick,
bounce, drop, arm, elbow, nod), joints (bone names guestlib can't find, by canonical name). A centaur: "gait":
"centaur", legs {front_l, front_r, rear_l, rear_r: bones from the shoulder / hip down to the hoof's end}, hips (its
body's root bone), head, torso (its rider half's root, optional), arms (a bone each, optional), tail (optional),
and gallop (guestlib.GALLOP overrides) instead of run. A horse: "gait": "horse", legs as a centaur's but four bones
each, from the shoulder / hip to the pastern (its hoof is found in its mesh), neck (bones), and gallop
(guestlib.HORSE overrides: neck, nod, head, pitch, hop, the key poses). trail: the loose parts that stream behind ({bone-name pattern: [share, lift, wave]},
guestlib.TRAIL without: a game rip's tail, hair and ribbons); bend: parts held turned whole from how they rest
(guestlib.find_bends: wings folded back). A model that came half skinned: hide, attach, skin,
squeeze (guestlib.fix_parts). A model with a run of its own: anim {"action": its name, "frames": [from, to]: one
stride}, played instead of making one (anything that moves on two "feet": joints names them, a crawler's hands
too). import: the importer's options (guestlib.load). parents: a flat rig's chain ({bone: parent}, guestlib.reparent).
lights: the hounds' lights turned up for a dark model ({"key": 2, "fill": 3}). x: where its hips sit across the frame
(0.5: the middle). stride: how far a stride carries it (px) instead of what its feet say: a walk's short stride plays
far too fast at race speed (the overlay steps the frames by the distance run), so it skates a little instead.
keep: a file with several characters in it, the materials of the one to keep (a pattern; guestlib.load). turn:
degrees to turn the side view about up, for a clip that runs another way than its rest faces (guestlib.Rig).
matte: no metal (a game rip whose packed map reads as metallic renders its cloth black; guestlib.matte). dither: its
blended materials drawn dithered (a body that showed its far side through in dark blotches; guestlib.dither). paint:
{material: image in its folder}, an old file's texture its material no longer reads; hue: {material: degrees}, its
colours turned round the wheel (guestlib.recolor).
run.knee -1: knees that bend backward (a walker, a bird); arms: false, a rig with no arms; run.out: arms swung wide
(a big belly); run.still: joints left as they stand (an arm holding a sword). rig: a model that came with no
skeleton gets one ({joint: [x, y, z] as it came, after move}, guestlib.make_rig), its parts in attach left unskinned;
hold: the loose pieces in a box move with one bone ({bone: [[x, y, z], [x, y, z]]}: the sword in its hand). move:
parts the file left out of place, put back first ({object: [x, y, z]}, world units: a hover's too). gait "hover":
something that floats, rolls or drives (see hover()): no skeleton needed, forward the way it faces as it came.
frame: a model whose parts its file moves about with no skeleton (a toy that transforms) stands as at that frame;
crop: only the parts whose middle is in that box stay ([[x, y, z], [x, y, z]], world, as it came: a file with two of
it). rigid: a rig made for a model of hard parts moves each loose piece with one bone (a robot). mirror: a guest with
its own run seen from its other side and mirrored (a skater who'd show us his back; his right foot leads instead).
morph: it changes shape partway through a race (render_morph.py, after this). Writes out/guests/<id>/run_NN.png,
anchors.json (with ppm: its px a metre) and sheet.png (to look at). No -Y: downloads can carry scripts.
"""
import bpy, sys, os, json, math
from mathutils import Matrix, Vector
sys.path.insert(0, os.path.dirname(__file__))
import houndlib as H
import guestlib as G
from bpy_extras.object_utils import world_to_camera_view

N = 16                          # frames in one stride (as the hounds' gallop)
FOOT = 0.06                     # the feet stand this share of the frame above its bottom

argv = sys.argv[sys.argv.index("--") + 1:]
SPEC, SRC, OUT = argv[0], argv[1], argv[2]
want = set(argv[3].split(",")) if len(argv) > 3 else None


def find_model(d):
    for ext in (".glb", ".gltf", ".fbx", ".blend"):
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(ext):
                return os.path.join(d, f)
    return None


def upright(arm, build):
    """turn the whole model the hounds' way: forward -Y (screen right), up +Z, its left +X (toward the back)."""
    rig = build()
    M = Matrix((rig.L, -rig.F, rig.U)).to_4x4()
    piv = bpy.data.objects.new("upright", None); bpy.context.scene.collection.objects.link(piv)
    for o in list(bpy.data.objects):
        if o.parent is None and o is not piv:
            mw = o.matrix_world.copy(); o.parent = piv; o.matrix_world = mw
    piv.matrix_world = M
    bpy.context.view_layer.update()
    return build()


def tall(rig):
    """how tall it stands (m): its highest vertex above the ground, at rest (its own run: in its first frame)."""
    return G.span(rig)[1] - rig.ground


def move_parts(spec):
    """move: parts the file left out of place, put back ({object: [x, y, z]}, world units, as it came)."""
    for name, d in spec.get("move", {}).items():
        o = bpy.data.objects[name]
        o.matrix_world = Matrix.Translation(Vector(d)) @ o.matrix_world
    bpy.context.view_layer.update()


def hover(spec, gid, path):
    """A guest that floats along (gait "hover": a podracer, a train, a car): it bobs and pitches (guestlib.HOVER, the
    spec's "hover": bob, pitch, waves, lift: how far above the ground it floats, a share of its height), plays its
    own clip if it has one (anim), and its feet never touch down. forward: the way it faces as it came (guestlib.AXES,
    "-y" by default). stride: how far one bob carries it (px; twice its length if not given). move: parts the file left
    out of place, {object: [x, y, z]} (world units, as it came). hover.roll: a rigged car's wheels turn that many
    times a bob (guestlib.wheel_bones finds them), and the stride is as far as that rolls it."""
    anim = spec.get("anim")
    arm = G.load(path, anim and anim["action"], spec.get("import"), spec.get("keep"), spec.get("frame"), spec.get("crop"))
    if spec.get("matte"):
        G.matte()
    if spec.get("dither"):
        G.dither()
    G.recolor(os.path.dirname(path), spec.get("paint"), spec.get("hue"))
    if arm and (spec.get("hide") or spec.get("attach")):
        G.fix_parts(arm, spec.get("hide", ()), spec.get("attach"))
    elif spec.get("hide"):                                # (no skeleton: hide its parts by name)
        for o in bpy.data.objects:
            if o.type == "MESH" and any(o.name.startswith(k) or (o.parent and o.parent.name.startswith(k)) for k in spec["hide"]):
                o.hide_render = True; o.hide_set(True)
    move_parts(spec)
    F0 = G.AXES[spec.get("forward", "-y")]
    turned = lambda: (bpy.data.objects["upright"].matrix_world.to_3x3() @ F0) if "upright" in bpy.data.objects else F0
    P = {**G.HOVER, **spec.get("hover", {})}
    if anim:
        bpy.context.scene.frame_set(int(anim["frames"][0]))
    body = upright(arm, lambda: G.Body(turned(), arm))
    piv = bpy.data.objects["upright"]
    rest = piv.matrix_world.copy()
    w, h = spec.get("w", 300), spec.get("h", 240)
    ppm = spec.get("height", 120) / (2 * body.leg)
    body.ground -= P["lift"] * 2 * body.leg               # (it floats: the ground is below it)
    cam = H.setup_render(w, h)
    for name, k in spec.get("lights", {}).items():
        bpy.data.lights[name].energy *= k
    G.face_camera(cam, body, w, h, ppm, FOOT, spec.get("x", 0.5))
    sc = bpy.context.scene

    def px(p):
        v = world_to_camera_view(sc, cam, p)
        return [round(v.x * w, 1), round((1 - v.y) * h, 1)]
    nose, c, U, F, k = G.front_point(body), body.at("Hips"), body.U, body.F, body.leg
    under = lambda p: p - U * (p.dot(U) - body.ground)     # (on the ground, below p)
    wheels = G.wheel_bones(arm, body) if arm and P.get("roll") else []
    if wheels:                                            # (they turn about its left, in its own space: it pitches)
        axle = (arm.matrix_world.inverted().to_3x3() @ body.L).normalized()
        basis = {n: arm.pose.bones[n].matrix_basis.copy() for n, _, _ in wheels}
        curves = G.keyed(arm, basis)
    od = os.path.join(OUT, gid); os.makedirs(od, exist_ok=True)
    frames, files = [], []
    for i in range(N):
        for n, m in (basis.items() if wheels else ()):
            arm.pose.bones[n].matrix_basis = m            # (a clip that doesn't key them all leaves the last turn)
        for fc in (curves if wheels else ()):
            fc.mute = False
        T = G.hover_pose(body, piv, rest, i / N, P)
        if anim:
            f0, f1 = anim["frames"]
            t = f0 + (f1 - f0) * i / N
            sc.frame_set(int(math.floor(t)), subframe=t - math.floor(t))
        if wheels:
            G.spin_wheels(arm, wheels, axle, 360 * P["roll"] * i / N, curves)
        hd, hp = body.now("Head"), body.now("Hips")
        front, back = under(c + F * body.length * 0.3), under(c - F * body.length * 0.3)
        fr = {"head": px(hd), "eye": px(hd - U * k * 0.1), "mouth": px(T @ nose), "hips": px(hp),
              "tail_base": px(T @ (c - F * body.length * 0.45)), "tail": px(T @ (c - F * body.length * 0.55)), "back": px(hp)}
        for key, p in (("LF", front), ("RF", front), ("LH", back), ("RH", back)):
            fr[key] = px(p) + [0]                          # (never down: no footfalls)
        frames.append(fr)
        sc.render.filepath = os.path.join(od, f"run_{i:02d}.png")
        bpy.ops.render.render(write_still=True); files.append(sc.render.filepath)
    an = {"w": w, "h": h, "ground": px(under(c))[1], "ppm": round(ppm, 3),
          "stride": spec.get("stride") or round((P["roll"] * 2 * math.pi * max(r for _, _, r in wheels) if wheels
                                                 else 2 * body.length) * ppm, 1),
          "nose": max(f["mouth"][0] for f in frames), "body": [round(sum(f["hips"][0] for f in frames) / N, 1),
                                                                round(sum(f["hips"][1] for f in frames) / N, 1)],
          "frames": frames, "family": "hover", "model": os.path.basename(path)}
    with open(os.path.join(od, "anchors.json"), "w", encoding="utf-8") as f:
        json.dump(an, f)
    H.grid(files, 8, w, h, os.path.join(od, "sheet.png"))
    print("GUEST", gid, "hover", os.path.basename(path), f"{ppm:.0f} px/m, stride {an['stride']} px",
          f"wheels {[(n, round(r * ppm, 1)) for n, _, r in wheels]}" if wheels else "")


for spec in json.load(open(SPEC, encoding="utf-8"))["guests"]:
    gid = spec["id"]
    if want and gid not in want:
        continue
    path = find_model(os.path.join(SRC, spec.get("model", gid)))
    if not path:
        print("GUEST", gid, "no model"); continue
    if spec.get("gait") == "hover":
        hover(spec, gid, path); continue
    anim = spec.get("anim")
    arm = G.load(path, anim and anim["action"], spec.get("import"), spec.get("keep"), spec.get("frame"), spec.get("crop"))
    move_parts(spec)                                    # (before a made rig: its joints are where the parts stand)
    made = {}
    if arm is None and spec.get("rig"):                 # (it came with no skeleton: one is made for it)
        arm, made = G.make_rig(spec["rig"], list(spec.get("attach", {})), spec.get("hold"), spec.get("rigid"))
    if spec.get("matte"):
        G.matte()
    if spec.get("dither"):
        G.dither()
    G.recolor(os.path.dirname(path), spec.get("paint"), spec.get("hue"))
    if spec.get("parents"):
        G.reparent(arm, spec["parents"])
    if spec.get("hide") or spec.get("attach") or spec.get("skin") or spec.get("squeeze"):
        G.fix_parts(arm, spec.get("hide", ()), spec.get("attach"), spec.get("skin"), spec.get("squeeze"))
    horse = spec.get("gait") == "horse"
    centaur = horse or spec.get("gait") == "centaur"
    if centaur:
        fam = spec["gait"]
        build = lambda: G.centaur(arm, spec["legs"], spec["hips"], spec["head"], spec.get("torso"), spec.get("arms"),
                                  spec.get("neck"))
    else:
        fam, joints = G.find_joints(arm, {**made, **spec.get("joints", {})})
        missing = [j for j in ("Hips", "Head", "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot")
                   + (() if anim or spec.get("arms") is False else ("LeftArm", "LeftForeArm", "RightArm", "RightForeArm"))
                   if j not in joints]
        if missing:
            print("GUEST", gid, fam, "missing joints", missing); continue
        build = lambda: G.Rig(arm, joints, level=bool(anim), turn=spec.get("turn", 0))
    rig = upright(arm, build)
    if anim and spec.get("mirror"):                    # (its other side, mirrored: left and right swap too)
        piv = bpy.data.objects["upright"]
        piv.matrix_world = Matrix.Scale(-1, 4, Vector((1, 0, 0))) @ piv.matrix_world
        bpy.context.view_layer.update()
        joints = {k.replace("Left", "Right") if "Left" in k else k.replace("Right", "Left"): v for k, v in joints.items()}
        rig = build()
    if anim:                                            # its own run: where its frames are, its ground and stride
        piv = bpy.data.objects["upright"]
        shots = G.own_run(rig, piv, anim["frames"], N)
        G.play(rig, piv, *shots[0])
    else:
        rig.loose = G.find_loose(rig, spec.get("trail"))
        rig.bends = G.find_bends(rig, spec.get("bend"))
    if horse:
        G.horse(rig)
    w, h = spec.get("w", 220), spec.get("h", 280)
    ppm = spec.get("height", 210) / tall(rig)
    if not anim and not centaur:                        # (its soles on the ground, not its feet joints: they sit a
        rig.ground = min(rig.ground, G.span(rig)[0])    # shoe's height up, and the frame cut its shoes off)
    cam = H.setup_render(w, h)
    for name, k in spec.get("lights", {}).items():     # (a dark model: the hounds' lights, turned up)
        bpy.data.lights[name].energy *= k
    G.face_camera(cam, rig, w, h, ppm, FOOT, spec.get("x", 0.5))
    sc = bpy.context.scene

    def px(p):
        v = world_to_camera_view(sc, cam, p)
        return [round(v.x * w, 1), round((1 - v.y) * h, 1)]
    P = {**(G.HORSE if horse else G.GALLOP if centaur else G.RUN), **spec.get("gallop" if centaur else "run", {})}
    od = os.path.join(OUT, gid); os.makedirs(od, exist_ok=True)
    frames, files = [], []
    tail_bone = next((b.name for b in arm.data.bones if spec.get("tail") and G.base(b.name) == spec["tail"]), None)
    gait = G.horse_gait(rig, N, P) if horse else None
    for i in range(N):
        if anim:
            G.play(rig, piv, *shots[i])
            down = {s: rig.down[s][i] for s in ("Left", "Right")}
        elif horse:
            down, hooves = G.horse_pose(rig, gait[i], i / N, P)
        else:
            down = G.gallop_pose(rig, i / N, P) if centaur else G.run_pose(rig, i / N, P)
        hd, hp = rig.now("Head"), rig.now("Hips")
        k = rig.leg * (0.5 if centaur else 1)              # (offsets about a human head's size)
        face = G.front_of_face(rig) or hd + rig.F * k * 0.12 + rig.U * k * 0.06
        tail = rig.pos[tail_bone] if tail_bone else hp - rig.F * k * 0.2
        fr = {"head": px(hd + rig.U * k * 0.12), "eye": px(hd + rig.U * k * 0.08), "mouth": px(face), "hips": px(hp),
              "tail_base": px(tail), "tail": px(tail - rig.F * k * 0.1), "back": px(hp)}
        if centaur:                                     # four hooves, the hounds' way
            for leg, key in (("front_l", "LF"), ("front_r", "RF"), ("rear_l", "LH"), ("rear_r", "RH")):
                fr[key] = px(hooves[leg] if horse else rig.pos[rig.legs[leg][-1]]) + [1 if down[leg] else 0]
        else:
            for side, key in (("Left", "L"), ("Right", "R")):
                ft = px(rig.now(side + "Foot")) + [1 if down[side] else 0]
                fr[key + "F"], fr[key + "H"] = ft, ft         # (two feet: the hounds' front and hind are the same)
        frames.append(fr)
        sc.render.filepath = os.path.join(od, f"run_{i:02d}.png")
        bpy.ops.render.render(write_still=True); files.append(sc.render.filepath)
    ground = px(rig.at("Hips") - rig.U * (rig.at("Hips").dot(rig.U) - rig.ground))[1]
    if anim or horse:                                   # how far a stride carries it
        travel = rig.travel
    else:                                               # (a foot's travel while down, over its share of the stride)
        travel = (2 * rig.leg * math.sin(math.radians(P["swing"])) if centaur else P["stride"] * rig.leg) / P["stance"]
    an = {"w": w, "h": h, "ground": ground, "ppm": round(ppm, 3),
          "stride": spec.get("stride") or round(travel * ppm, 1),
          "nose": max(f["mouth"][0] for f in frames), "body": [round(sum(f["hips"][0] for f in frames) / N, 1),
                                                                round(sum(f["hips"][1] for f in frames) / N, 1)],
          "frames": frames, "family": fam, "model": os.path.basename(path)}
    with open(os.path.join(od, "anchors.json"), "w", encoding="utf-8") as f:
        json.dump(an, f)
    H.grid(files, 8, w, h, os.path.join(od, "sheet.png"))
    print("GUEST", gid, fam, os.path.basename(path), f"{ppm:.0f} px/m, stride {an['stride']} px, {len(rig.loose)} loose bones")
