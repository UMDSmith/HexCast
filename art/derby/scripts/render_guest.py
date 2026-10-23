"""Render the guest racers: each one running, one stride, seen from the side like the hounds.

    blender -b -P art/derby/scripts/render_guest.py -- <spec.json> <abs path>/art/derby/source/guests <abs path>/art/derby/out/guests [ids]

spec.json: art/derby/guests.json (the add-on's guests) or a local pack's (art/derby/source/guests/local.json: it
never ships). Each guest: model (its folder in source/guests; the first .glb/.gltf/.fbx in it), height (px tall
standing in the frame), w / h (the frame, px), run (guestlib.RUN overrides: lean, stride, stance, lift, kick,
bounce, drop, arm, elbow, nod), joints (bone names guestlib can't find, by canonical name). A centaur: "gait":
"centaur", legs {front_l, front_r, rear_l, rear_r: bones from the shoulder / hip down to the hoof's end}, hips (its
body's root bone), head, torso (its rider half's root, optional), arms (a bone each, optional), tail (optional),
and gallop (guestlib.GALLOP overrides) instead of run. trail: the loose parts that stream behind ({bone-name pattern: [share, lift, wave]},
guestlib.TRAIL without: a game rip's tail, hair and ribbons). A model that came half skinned: hide, attach, skin,
squeeze (guestlib.fix_parts). A model with a run of its own: anim {"action": its name, "frames": [from, to]: one
stride}, played instead of making one (anything that moves on two "feet": joints names them, a crawler's hands
too). import: the importer's options (guestlib.load). parents: a flat rig's chain ({bone: parent}, guestlib.reparent).
lights: the hounds' lights turned up for a dark model ({"key": 2, "fill": 3}). x: where its hips sit across the frame
(0.5: the middle). stride: how far a stride carries it (px) instead of what its feet say: a walk's short stride plays
far too fast at race speed (the overlay steps the frames by the distance run), so it skates a little instead.
Writes out/guests/<id>/run_NN.png, anchors.json and sheet.png (to look at). No -Y: downloads can carry scripts.
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
    for ext in (".glb", ".gltf", ".fbx"):
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


for spec in json.load(open(SPEC, encoding="utf-8"))["guests"]:
    gid = spec["id"]
    if want and gid not in want:
        continue
    path = find_model(os.path.join(SRC, spec.get("model", gid)))
    if not path:
        print("GUEST", gid, "no model"); continue
    anim = spec.get("anim")
    arm = G.load(path, anim and anim["action"], spec.get("import"))
    if spec.get("parents"):
        G.reparent(arm, spec["parents"])
    if spec.get("hide") or spec.get("attach") or spec.get("skin") or spec.get("squeeze"):
        G.fix_parts(arm, spec.get("hide", ()), spec.get("attach"), spec.get("skin"), spec.get("squeeze"))
    centaur = spec.get("gait") == "centaur"
    if centaur:
        fam = "centaur"
        build = lambda: G.centaur(arm, spec["legs"], spec["hips"], spec["head"], spec.get("torso"), spec.get("arms"))
    else:
        fam, joints = G.find_joints(arm, spec.get("joints"))
        missing = [j for j in ("Hips", "Head", "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot")
                   + (() if anim else ("LeftArm", "LeftForeArm", "RightArm", "RightForeArm")) if j not in joints]
        if missing:
            print("GUEST", gid, fam, "missing joints", missing); continue
        build = lambda: G.Rig(arm, joints, level=bool(anim))
    rig = upright(arm, build)
    if anim:                                            # its own run: where its frames are, its ground and stride
        piv = bpy.data.objects["upright"]
        shots = G.own_run(rig, piv, anim["frames"], N)
        G.play(rig, piv, *shots[0])
    else:
        rig.loose = G.find_loose(rig, spec.get("trail"))
    w, h = spec.get("w", 220), spec.get("h", 280)
    ppm = spec.get("height", 210) / tall(rig)
    cam = H.setup_render(w, h)
    for name, k in spec.get("lights", {}).items():     # (a dark model: the hounds' lights, turned up)
        bpy.data.lights[name].energy *= k
    G.face_camera(cam, rig, w, h, ppm, FOOT, spec.get("x", 0.5))
    sc = bpy.context.scene

    def px(p):
        v = world_to_camera_view(sc, cam, p)
        return [round(v.x * w, 1), round((1 - v.y) * h, 1)]
    P = {**(G.GALLOP if centaur else G.RUN), **spec.get("gallop" if centaur else "run", {})}
    od = os.path.join(OUT, gid); os.makedirs(od, exist_ok=True)
    frames, files = [], []
    tail_bone = next((b.name for b in arm.data.bones if spec.get("tail") and G.base(b.name) == spec["tail"]), None)
    for i in range(N):
        if anim:
            G.play(rig, piv, *shots[i])
            down = {s: rig.down[s][i] for s in ("Left", "Right")}
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
                fr[key] = px(rig.pos[rig.legs[leg][-1]]) + [1 if down[leg] else 0]
        else:
            for side, key in (("Left", "L"), ("Right", "R")):
                ft = px(rig.now(side + "Foot")) + [1 if down[side] else 0]
                fr[key + "F"], fr[key + "H"] = ft, ft         # (two feet: the hounds' front and hind are the same)
        frames.append(fr)
        sc.render.filepath = os.path.join(od, f"run_{i:02d}.png")
        bpy.ops.render.render(write_still=True); files.append(sc.render.filepath)
    ground = px(rig.at("Hips") - rig.U * (rig.at("Hips").dot(rig.U) - rig.ground))[1]
    travel = 2 * rig.leg * math.sin(math.radians(P["swing"])) if centaur else P["stride"] * rig.leg   # a foot, while down
    an = {"w": w, "h": h, "ground": ground,
          "stride": spec.get("stride") or round((rig.travel if anim else travel / P["stance"]) * ppm, 1),
          "nose": max(f["mouth"][0] for f in frames), "body": [round(sum(f["hips"][0] for f in frames) / N, 1),
                                                                round(sum(f["hips"][1] for f in frames) / N, 1)],
          "frames": frames, "family": fam, "model": os.path.basename(path)}
    with open(os.path.join(od, "anchors.json"), "w", encoding="utf-8") as f:
        json.dump(an, f)
    H.grid(files, 8, w, h, os.path.join(od, "sheet.png"))
    print("GUEST", gid, fam, os.path.basename(path), f"{ppm:.0f} px/m, stride {an['stride']} px, {len(rig.loose)} loose bones")
