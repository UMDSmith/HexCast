"""A guest that changes shape partway through a race (Optimus Grime: robot to truck): the change, once, and the
shape it races on in, from the file's own animation of it.

    blender -b -P art/derby/scripts/render_morph.py -- <spec.json> <abs path>/art/derby/source/guests <abs path>/art/derby/out/guests [ids]

Render the guest first (render_guest.py: out/guests/<id>, its first shape; this reads its size and its nose from
there). The spec's "morph": frames [from, to] (the file's frames the change takes: the parts its animation moves
are the ones that change, the rest is hidden; from after to plays the file's change backward: a car that turns into a
robot, the robot turning into the car), heading [x, y] (the way the new shape faces at the end, as it came:
it is turned that way bit by bit as it changes, so it ends facing the way the race runs), w / h / front (the frame,
px, and where its front is held across it: the change is pinned there, so it doesn't wander off as it flips), grow
(how big the new shape is against the file's own, 1: a truck twice its size is 2; it grows bit by bit as it
changes), n (frames, 16), and then {w, h, front, stride, bob, level, roll}: the new shape's frame, how far a cycle
carries it (px), how much it bounces (px), level (true: stood flat on its wheels, a file that leaves its truck tipped,
levelled bit by bit as it changes) and roll (its wheels turn that many times a cycle, and the stride is as far as that
rolls it: 2 for a rim of ten holes steps them a quarter of the way to the next each frame, forward; find_wheels, or
for wheels skinned to a skeleton, guestlib.wheel_bones: 1 for Sideswipe's five spokes). at [from, to] (the share of the race it changes at, picked per race) and dur (seconds) are
the overlay's (which also changes it, back and forth, whenever something on the track slows it).

Writes out/guests/<id>__morph (the change, played once) and out/guests/<id>__then (the new shape running), each
run_NN.png + anchors.json + sheet.png, at the first shape's px a metre; export_assets.py packs them with it.
No -Y: downloads can carry scripts.
"""
import bpy, sys, os, json, math
from types import SimpleNamespace
import numpy as np
from mathutils import Matrix, Quaternion, Vector
sys.path.insert(0, os.path.dirname(__file__))
import houndlib as H
import guestlib as G

FOOT = 0.06                     # the feet stand this share of the frame above its bottom (as render_guest.py)

argv = sys.argv[sys.argv.index("--") + 1:]
SPEC, SRC, OUT = argv[0], argv[1], argv[2]
want = set(argv[3].split(",")) if len(argv) > 3 else None
smooth = lambda u: u * u * (3 - 2 * u)


def find_model(d):
    for ext in (".glb", ".gltf", ".fbx", ".blend"):
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(ext):
                return os.path.join(d, f)
    return None


def corners(objs):
    return [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]


def ahead(first):
    """how far the first shape reaches past its nose, on the whole (px): its frontmost solid pixel, each frame."""
    an = json.load(open(os.path.join(first, "anchors.json"), encoding="utf-8"))
    out = []
    for i in range(len(an["frames"])):
        im = bpy.data.images.load(os.path.join(first, f"run_{i:02d}.png"))
        a = np.array(im.pixels[:], dtype=np.float32).reshape(im.size[1], im.size[0], 4)[:, :, 3]
        bpy.data.images.remove(im)
        cols = np.nonzero((a > 0.5).any(axis=0))[0]
        if len(cols):
            out.append(float(cols.max()) - an["nose"])
    return an, sum(out) / max(1, len(out))


def find_wheels(parts, h):
    """the wheels of the shape as it stands (h: its heading): the parts round from the side (about as long as they
    are tall) low on it, each with the biggest (its tyre) whose middle and axle it shares. Returns [(parts, middle,
    radius)] and the up of the ground they stand on (the plane through their middles: None with fewer than three
    wheels not in a line)."""
    dg = bpy.context.evaluated_depsgraph_get()
    pts = {}
    for o in parts:
        e = o.evaluated_get(dg); m = e.to_mesh(); mw = e.matrix_world
        ps = np.array([tuple(mw @ v.co) for v in m.vertices]); e.to_mesh_clear()
        if len(ps) >= 8:
            pts[o] = ps
    if not pts:
        return [], None
    z = np.concatenate([p[:, 2] for p in pts.values()]); zlo, zhi = float(z.min()), float(z.max())
    hv = np.array(tuple(h))
    span = lambda p, a: float((p @ a).max() - (p @ a).min())
    round_ = {o: p for o, p in pts.items() if span(p, hv) > 1e-4 and float(p[:, 2].min()) < zlo + 0.3 * (zhi - zlo)
              and abs(span(p, hv) - span(p, np.array((0, 0, 1)))) < 0.25 * max(span(p, hv), span(p, np.array((0, 0, 1))))}
    if not round_:
        return [], None
    big = max(span(p, np.array((0, 0, 1))) for p in round_.values())
    tyres = []
    for o, p in round_.items():                        # (a tyre is the roundest of all: a mudguard can be nearly as big)
        a, b = span(p, hv), span(p, np.array((0, 0, 1)))
        if b >= 0.8 * big and abs(a - b) < 0.12 * max(a, b):
            c = (p.min(axis=0) + p.max(axis=0)) / 2
            axle = np.linalg.svd(p - p.mean(axis=0), full_matrices=False)[2][2]           # (its thinnest way)
            tyres.append({"parts": [o], "mid": c, "axle": axle, "r": (span(p, hv) + span(p, np.array((0, 0, 1)))) / 4})
    for o, p in round_.items():
        if any(o in t["parts"] for t in tyres):
            continue
        c = (p.min(axis=0) + p.max(axis=0)) / 2
        for t in sorted(tyres, key=lambda t: float(np.linalg.norm(c - t["mid"]))):
            d = c - t["mid"]; along = float(d @ t["axle"])
            if abs(along) < t["r"] and float(np.linalg.norm(d - along * t["axle"])) < 0.25 * t["r"]:
                t["parts"].append(o); break
    up = None
    if len(tyres) >= 3:
        C = np.array([t["mid"] for t in tyres])
        s = np.linalg.svd(C - C.mean(axis=0), full_matrices=False)
        if s[1][1] > 0.1 * s[1][0]:
            n = s[2][2]; up = Vector(tuple(n if n[2] > 0 else -n)).normalized()
    return [(t["parts"], Vector(tuple(t["mid"])), t["r"]) for t in tyres], up


def morph(spec, gid, path):
    M = spec["morph"]
    first = os.path.join(OUT, gid)
    an0, d0 = ahead(first)
    ppm = an0["ppm"]
    f0, f1 = M["frames"]
    n = M.get("n", 16)
    bpy.ops.wm.read_factory_settings(use_empty=True)     # (not guestlib.load: that stops its animation)
    if path.lower().endswith(".blend"):
        G.open_blend(path)
    else:
        bpy.ops.import_scene.gltf(filepath=path, **(spec.get("import") or {}))
    for o in [o for o in bpy.data.objects if o.type in ("CAMERA", "LIGHT")]:
        bpy.data.objects.remove(o)
    sc = bpy.context.scene
    sc.frame_set(int(f0)); bpy.context.view_layer.update()
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    mid = lambda o: sum(corners([o]), Vector()) / 8
    at0 = {o: mid(o) for o in meshes}
    sc.frame_set(int(f1)); bpy.context.view_layer.update()
    moving = [o for o in meshes if (mid(o) - at0[o]).length > 0.01]
    for o in meshes:                                   # (the parts it doesn't move: another copy of it, standing by)
        if o not in moving:
            o.hide_render = True
    piv = bpy.data.objects.new("pivot", None); sc.collection.objects.link(piv)
    for o in list(bpy.data.objects):
        if o.parent is None and o is not piv:
            o.parent = piv; o.matrix_parent_inverse = Matrix()
    hx, hy = M["heading"]
    turn = (-90 - math.degrees(math.atan2(hy, hx)) + 180) % 360 - 180   # (from its heading to -Y: the race's way)
    low = {}
    for f in (f0, f1):
        sc.frame_set(int(f)); bpy.context.view_layer.update()
        low[f] = min(p.z for p in corners(moving))

    grow = M.get("grow", 1.0)
    T = M.get("then", {})
    h1 = Vector((hx, hy, 0)).normalized()
    sc.frame_set(int(f1)); bpy.context.view_layer.update()
    wheels, up = find_wheels(moving, h1) if T.get("level") or T.get("roll") else ([], None)
    lev = up.rotation_difference(Vector((0, 0, 1))) if T.get("level") and up else None
    n_up = up or Vector((0, 0, 1))
    fwd = (h1 - n_up * h1.dot(n_up)).normalized()
    axle = n_up.cross(fwd).normalized()               # (turning about it, its tops go forward)
    bones = []                                         # (wheels skinned to a skeleton: its bones turn them)
    if T.get("roll"):
        body = SimpleNamespace(U=n_up, F=fwd, L=axle, ground=low[f1])
        for arm in [o for o in bpy.data.objects if o.type == "ARMATURE"]:
            wb = G.wheel_bones(arm, body, moving)
            if wb:
                bones.append((arm, wb, (arm.matrix_world.inverted().to_3x3() @ axle).normalized(),
                              {n: arm.pose.bones[n].matrix_basis.copy() for n, _, _ in wb}, G.keyed(arm, [n for n, _, _ in wb])))
    if lev:                                            # (its tyres' ground: the plane under their middles)
        bottom = sum((m for _, m, _ in wheels), Vector()) / len(wheels) - up * (sum(r for _, _, r in wheels) / len(wheels))

    def stand(f, s, front, w):
        """the moving parts at frame f, turned, grown and levelled s of the way, on the ground, their front at px
        `front` of w."""
        piv.matrix_world = Matrix()
        sc.frame_set(int(math.floor(f)), subframe=f - math.floor(f)); bpy.context.view_layer.update()
        c = sum(corners(moving), Vector()) / (8 * len(moving))
        g1 = low[f1]
        if lev:                                        # (where its tyres' ground is, under its middle)
            g1 = bottom.z - (up.x * (c.x - bottom.x) + up.y * (c.y - bottom.y)) / up.z
        g = low[f0] + (g1 - low[f0]) * s               # (its ground, from the first shape's to the last's)
        level = Quaternion().slerp(lev, s).to_matrix().to_4x4() if lev else Matrix()
        R = (Matrix.Translation(Vector((c.x, c.y, g))) @ Matrix.Rotation(math.radians(turn * s), 4, "Z")
             @ Matrix.Scale(1 + (grow - 1) * s, 4) @ level @ Matrix.Translation(Vector((-c.x, -c.y, -g))))
        piv.matrix_world = R; bpy.context.view_layer.update()
        y_front = min(p.y for p in corners(moving))     # (the race runs toward -Y: screen right)
        dy = -(front - w / 2) / ppm - y_front            # (the camera's middle is at y 0)
        piv.matrix_world = Matrix.Translation(Vector((0, dy, -g))) @ R    # (its ground at z 0)
        bpy.context.view_layer.update()

    def shoot(od, w, h, front, frames, stride, pose):
        os.makedirs(od, exist_ok=True)
        cam = H.setup_render(w, h)
        for name, k in spec.get("lights", {}).items():
            bpy.data.lights[name].energy *= k
        ground = round(h * (1 - FOOT), 1)
        cam.data.ortho_scale = max(w, h) / ppm
        cam.location = (-30, 0, (ground - h / 2) / ppm)  # (z 0 at the ground's px)
        px = lambda p: (w / 2 - p.y * ppm, h / 2 - (p.z - cam.location.z) * ppm)
        out, files = [], []
        for i in range(frames):
            pose(i)
            ps = [px(p) for p in corners(moving)]
            x0, x1 = min(p[0] for p in ps), max(p[0] for p in ps)
            y0, y1 = min(p[1] for p in ps), max(p[1] for p in ps)
            W, Hh = x1 - x0, y1 - y0
            r = lambda x, y: [round(x, 1), round(y, 1)]
            fr = {"head": r(x1 - 0.12 * W, y0 + 0.12 * Hh), "eye": r(x1 - 0.12 * W, y0 + 0.1 * Hh), "mouth": r(x1 - 4, y0 + 0.35 * Hh),
                  "hips": r((x0 + x1) / 2, (y0 + y1) / 2), "tail_base": r(x0 + 0.05 * W, (y0 + y1) / 2), "tail": r(x0, (y0 + y1) / 2),
                  "back": r((x0 + x1) / 2, y0)}
            for key, x in (("LF", x1 - 0.15 * W), ("RF", x1 - 0.15 * W), ("LH", x0 + 0.15 * W), ("RH", x0 + 0.15 * W)):
                fr[key] = r(x, ground) + [0]             # (rolls or flies: no footfalls)
            out.append(fr)
            sc.render.filepath = os.path.join(od, f"run_{i:02d}.png")
            bpy.ops.render.render(write_still=True); files.append(sc.render.filepath)
        an = {"w": w, "h": h, "ground": ground, "stride": stride, "nose": front, "ppm": ppm,
              "body": [round(sum(f["hips"][0] for f in out) / frames, 1), round(sum(f["hips"][1] for f in out) / frames, 1)],
              "frames": out, "family": "hover", "model": os.path.basename(path)}
        with open(os.path.join(od, "anchors.json"), "w", encoding="utf-8") as fh:
            json.dump(an, fh)
        H.grid(files, 8, w, h, os.path.join(od, "sheet.png"))
        for o in [o for o in bpy.data.objects if o.type in ("CAMERA", "LIGHT")]:
            bpy.data.objects.remove(o)

    # the change: its front pinned where the first shape's front is (ahead of its nose), easing onto its nose
    w, h, front = M.get("w", 380), M.get("h", 380), M.get("front", 330)
    shoot(os.path.join(OUT, gid + "__morph"), w, h, front, n, 0,
          lambda i: stand(f0 + (f1 - f0) * i / (n - 1), smooth(i / (n - 1)), front + d0 * (1 - smooth(i / (n - 1))), w))
    # the new shape, racing on: as the change left it, bouncing, its wheels turning (roll times a cycle)
    tw, th, tf, bob = T.get("w", 340), T.get("h", 200), T.get("front", 320), T.get("bob", 2)
    spin = T.get("roll", 0) if wheels or bones else 0
    rest = {o: o.matrix_basis.copy() for parts, _, _ in wheels for o in parts}
    stride = T.get("stride", 400)
    if spin:                                           # (as far as its wheels roll: the biggest)
        stride = round(spin * 2 * math.pi * max(r for *_, r in wheels + [w for b in bones for w in b[1]]) * grow * ppm, 1)

    def roll(i):
        u = 2 * math.pi * i / 16
        for o, m in rest.items():
            o.matrix_basis = m
        for arm, _, _, basis, curves in bones:         # (the clip poses them again, then they turn)
            for name, m in basis.items():
                arm.pose.bones[name].matrix_basis = m
            for fc in curves:
                fc.mute = False
        stand(f1, 1.0, tf, tw)
        piv.matrix_world = Matrix.Translation(Vector((0, 0, bob / ppm * (0.5 + 0.5 * math.sin(2 * u))))) @ piv.matrix_world
        bpy.context.view_layer.update()
        if spin:
            P = piv.matrix_world.copy(); Pi = P.inverted()
            for parts, mid, _ in wheels:
                S = Matrix.Translation(mid) @ Matrix.Rotation(spin * u, 4, axle) @ Matrix.Translation(-mid)
                for o in parts:
                    o.matrix_world = P @ S @ Pi @ o.matrix_world
            for arm, wb, a, _, curves in bones:
                G.spin_wheels(arm, wb, a, math.degrees(spin * u), curves)
            bpy.context.view_layer.update()
    shoot(os.path.join(OUT, gid + "__then"), tw, th, tf, 16, stride, roll)
    print("MORPH", gid, f"frames {f0}-{f1}, turned {turn:.1f} deg, grown x{grow}, {len(moving)} parts move, front ahead of nose {d0:.1f} px, {ppm:.0f} px/m",
          f"| {len(wheels)} wheels ({sum(len(p) for p, _, _ in wheels)} parts), level {math.degrees(lev.angle):.1f} deg" if wheels and lev else
          f"| {len(wheels)} wheels" if wheels else "", f"| wheel bones {[n for b in bones for n, _, _ in b[1]]}" if bones else "",
          f", rolling {spin} a cycle, stride {stride} px" if spin else "")


for spec in json.load(open(SPEC, encoding="utf-8"))["guests"]:
    gid = spec["id"]
    if not spec.get("morph") or (want and gid not in want):
        continue
    path = find_model(os.path.join(SRC, spec.get("model", gid)))
    morph(spec, gid, path)
