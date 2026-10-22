"""Hungry Harry: the Hell Hound's skeleton, nothing else.

Every bone is a rigid piece fixed to one DEF bone of the hound's rig (an Armature modifier with a single
weight-1 group per piece), so Harry runs with exactly the same gallop as the others. The skull is the
University of Edinburgh's CT scan of a dog skull (CC BY, see ../README.md); everything else, the lower
jaw included (the scan has none), is built here.
"""
import bpy, bmesh, math
import numpy as np
from mathutils import Matrix, Vector

BONE_COLOUR = (0.80, 0.74, 0.60, 1.0)
SOUL_COLOUR = (0.35, 1.0, 0.75, 1.0)


# ---- geometry helpers: each adds verts to a bmesh and returns them -------------------------------
def _align(a, b):
    """matrix placing a Z-aligned primitive of the right length between a and b."""
    d = b - a
    q = d.normalized().to_track_quat("Z", "Y")
    return Matrix.Translation((a + b) / 2) @ q.to_matrix().to_4x4()


def capsule(bm, a, b, r, r2=None, segs=8):
    a, b = Vector(a), Vector(b)
    if (b - a).length < 1e-4:
        return sphere(bm, a, r)
    out = bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r, radius2=r2 if r2 is not None else r,
                                depth=(b - a).length, matrix=_align(a, b))["verts"]
    return out + sphere(bm, a, r * 1.05, 6) + sphere(bm, b, (r2 if r2 is not None else r) * 1.05, 6)


def sphere(bm, c, r, segs=8):
    return bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=max(4, segs // 2 + 1), radius=r,
                                     matrix=Matrix.Translation(Vector(c)))["verts"]


def cone(bm, base, tip, r):
    base, tip = Vector(base), Vector(tip)
    return bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=r, radius2=0.0,
                                 depth=(tip - base).length, matrix=_align(base, tip))["verts"]


def block(bm, centre, size, rot=Matrix.Identity(4)):
    m = Matrix.Translation(Vector(centre)) @ rot @ Matrix.Diagonal((*size, 1.0))
    return bmesh.ops.create_cube(bm, size=1.0, matrix=m)["verts"]


def chain(bm, pts, r, r_end=None):
    out = []
    n = len(pts) - 1
    for i in range(n):
        ra = r + ((r_end if r_end is not None else r) - r) * i / n
        rb = r + ((r_end if r_end is not None else r) - r) * (i + 1) / n
        out += capsule(bm, pts[i], pts[i + 1], ra, rb, 6)
    return out


# ---- the skeleton --------------------------------------------------------------------------------
class Builder:
    def __init__(self, rig):
        self.rig = rig
        self.B = rig.data.bones
        self.bm = bmesh.new()
        self.groups = {}                       # bone name -> [vert]
        self.soul = []                         # verts of the glowing bits
        self.dl = self.bm.verts.layers.deform.verify()

    def head(self, n):
        return self.B[n].head_local.copy()

    def tail(self, n):
        return self.B[n].tail_local.copy()

    def add(self, bone, verts, soul=False):
        self.groups.setdefault(bone, []).extend(verts)
        if soul:
            self.soul.extend(verts)

    def finish(self, name, coll):
        me = bpy.data.meshes.new(name)
        bm = self.bm
        self.dl = bm.verts.layers.deform.verify()      # merging the skull in may have changed the layers
        idx = {}
        for i, b in enumerate(sorted(self.groups)):
            idx[b] = i
        for b, vs in self.groups.items():
            for v in vs:
                if v.is_valid:
                    v[self.dl][idx[b]] = 1.0
        soul = {v for v in self.soul if v.is_valid}
        bm.faces.ensure_lookup_table()
        for f in bm.faces:
            f.material_index = 1 if all(v in soul for v in f.verts) else 0
            f.smooth = True
        bm.to_mesh(me); bm.free()
        ob = bpy.data.objects.new(name, me)
        coll.objects.link(ob)
        for b in sorted(idx, key=idx.get):
            ob.vertex_groups.new(name=b)
        ob.parent = self.rig
        mod = ob.modifiers.new("rig", "ARMATURE"); mod.object = self.rig
        bone = bpy.data.materials.new("bone"); bone.use_nodes = True
        bsdf = bone.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = BONE_COLOUR
        bsdf.inputs["Roughness"].default_value = 0.6
        glow = bpy.data.materials.new("soul"); glow.use_nodes = True
        g = glow.node_tree.nodes["Principled BSDF"]
        g.inputs["Base Color"].default_value = SOUL_COLOUR
        g.inputs["Emission Color"].default_value = SOUL_COLOUR
        g.inputs["Emission Strength"].default_value = 8.0
        me.materials.append(bone); me.materials.append(glow)
        return ob


LIMB_WORDS = ("thigh", "shin", "foot", "toe", "shoulder", "breast", "pelvis", "palm", "index", "middle",
              "ring", "pinky", "Chain")


def _section(body, y, half=0.09):
    """the hound's torso cross-section near y (rest pose, armature space): [(x, z)] on the +X side.
    Skin that belongs mostly to a leg (or the collar) is left out, or ribs would run down the legs."""
    M = body.matrix_world
    names = {g.index: g.name for g in body.vertex_groups}
    pts = []
    for v in body.data.vertices:
        p = M @ v.co
        if abs(p.y - y) >= half or p.x < 0:
            continue
        top = max(v.groups, key=lambda g: g.weight, default=None)
        if top is None or any(w in names[top.group] for w in LIMB_WORDS):
            continue
        pts.append((p.x, p.z))
    return pts


def _rib_curve(body, y, spine_z, z_floor):
    """an arc from the spine round the +X side of the body at y, a little inside the skin."""
    pts = _section(body, y)
    if len(pts) < 12:
        return None
    zs = [p[1] for p in pts if p[1] > z_floor]
    top, bot = min(spine_z, max(zs)), max(min(zs), z_floor)
    zc = (top + bot) / 2
    out = []
    for i in range(9):
        ang = math.radians(80 - i * 20)               # from just below the spine round to the belly
        dirx, dirz = math.cos(ang), math.sin(ang)
        best = 0.0
        for x, z in pts:                                # the skin's distance from the centre in this direction
            dx, dz = x, z - zc
            dist = math.hypot(dx, dz)
            if dist > 1e-6 and (dx * dirx + dz * dirz) / dist > 0.97:
                best = max(best, dist)
        if best == 0.0:
            continue
        r = best * 0.82
        out.append(Vector((max(0.12, dirx * r), y, zc + dirz * r)))
    return out if len(out) >= 5 else None


def build(rig, skull_path, coll=None):
    """hide the hound's flesh and give it a skeleton. Returns the skeleton object."""
    coll = coll or bpy.context.scene.collection
    body = bpy.data.objects["cerber remesh.001"]
    for o in bpy.data.objects:          # no flesh, no eyes, and no collar (it hung round nothing, dark blobs by the skull)
        if o.type == "MESH" and o.name in ("cerber remesh.001", "Red Blackout Stylised", "collar"):
            o.hide_render = True; o.hide_viewport = True
    k = Builder(rig)
    bm = k.bm

    def up_of(d):                                      # perpendicular to d in the side plane, pointing up
        u = Vector((0, -d.z, d.y)) if d.y < 0 else Vector((0, d.z, -d.y))
        return u.normalized() if u.z >= 0 else -u.normalized()

    # backbone, neck and tail vertebrae, sitting a little above the bone line (nearer the back)
    spine = [("DEF-spine.004", 0.17, 0.42), ("DEF-spine.005", 0.17, 0.46), ("DEF-spine.006", 0.17, 0.5),
             ("DEF-spine.007", 0.18, 0.62), ("DEF-spine.008", 0.17, 0.5), ("DEF-spine.009", 0.15, 0.28),
             ("DEF-spine.010", 0.15, 0.22)]
    for n, rv, spike in spine:
        a, b = k.head(n), k.tail(n)
        d = b - a
        u = up_of(d)
        steps = max(2, int(d.length / 0.32))
        for i in range(steps):
            c = a + d * ((i + 0.5) / steps) + u * 0.35
            vs = capsule(bm, c - d.normalized() * 0.12, c + d.normalized() * 0.12, rv, rv, 8)
            vs += capsule(bm, c, c + u * spike + d.normalized() * 0.08, 0.05, 0.03, 5)     # spinous process
            for side in (-1, 1):                                                        # transverse processes
                vs += capsule(bm, c, c + Vector((side * rv * 2.0, 0, -0.05)), 0.04, 0.025, 5)
            k.add(n, vs)
    tail = ["DEF-spine.003", "DEF-spine.002", "DEF-spine.001", "DEF-spine"]
    for j, n in enumerate(tail):
        a, b = k.tail(n), k.head(n)                    # base -> tip (the chain is reversed)
        d = b - a
        steps = max(2, int(d.length / 0.3))
        for i in range(steps):
            f = (j + (i + 0.5) / steps) / len(tail)
            r = 0.13 * (1 - f) + 0.04
            c = a + d * ((i + 0.5) / steps)
            k.add(n, capsule(bm, c - d.normalized() * 0.1, c + d.normalized() * 0.1, r, r * 0.9, 6))

    # ribs round the chest, swept back toward the belly, and the breastbone joining them
    ribs_y = [1.95 + i * 0.22 for i in range(7)]       # the ribcage ends before the belly and the hips
    floor = 1.7
    ends = []
    for y in ribs_y:
        bone = "DEF-spine.007" if y < 2.96 else ("DEF-spine.006" if y < 3.70 else "DEF-spine.005")
        a, b = k.head(bone), k.tail(bone)
        f = (a.y - y) / (a.y - b.y) if a.y != b.y else 0.5
        spine_z = a.z + (b.z - a.z) * f + 0.3
        curve = _rib_curve(body, y, spine_z, floor)
        if not curve:
            continue
        span = max(1e-3, curve[0].z - curve[-1].z)
        for side in (1, -1):
            pts = [Vector((side * p.x, p.y + 0.3 * (curve[0].z - p.z) / span, p.z)) for p in curve]
            pts.insert(0, Vector((side * 0.1, y, spine_z)))
            k.add(bone, chain(bm, pts, 0.07, 0.05))
        ends.append(Vector((0, curve[-1].y + 0.3, curve[-1].z)))
    if len(ends) > 2:
        sternum = [e for e in ends[: len(ends) * 2 // 3]]
        k.add("DEF-spine.007", chain(bm, sternum, 0.08))

    # pelvis: the hip sockets, the wings reaching up toward the spine, the seat bones behind
    for side in ("L", "R"):
        s = 1 if side == "L" else -1
        hip = k.head(f"DEF-thigh.{side}")
        vs = sphere(bm, hip, 0.2, 8)
        vs += capsule(bm, hip, Vector((s * 0.35, hip.y - 0.55, hip.z + 0.65)), 0.16, 0.22, 8)      # ilium
        vs += capsule(bm, hip, Vector((s * 0.3, hip.y + 0.75, hip.z - 0.05)), 0.12, 0.1, 8)        # ischium
        k.add("DEF-spine.005", vs)
    # shoulder blades: a flat plate from the shoulder joint up and back over the ribs
    for side in ("L", "R"):
        s = 1 if side == "L" else -1
        j = k.head(f"DEF-front_thigh.{side}")
        top = Vector((s * 0.55, j.y + 0.75, j.z + 1.05))
        mid = (j + top) / 2
        d = top - j
        rot = d.normalized().to_track_quat("Z", "X").to_matrix().to_4x4()
        k.add(f"DEF-shoulder.{side}", block(bm, mid, (0.06, 0.55, d.length), rot) + sphere(bm, j, 0.16, 8))

    # legs: a long bone per segment with knobbly joints, toe bones and claws
    legs = {"thigh": 0.13, "thigh.001": 0.12, "shin": 0.1, "shin.001": 0.09, "foot": 0.085, "foot.001": 0.075,
            "toe": 0.06, "front_thigh": 0.13, "front_thigh.001": 0.12, "front_shin": 0.11, "front_shin.001": 0.1,
            "front_foot": 0.09, "front_foot.001": 0.075, "front_toe": 0.06}
    for side in ("L", "R"):
        for part, r in legs.items():
            n = f"DEF-{part.split('.')[0]}.{side}" + ("." + part.split(".")[1] if "." in part else "")
            if n not in k.B:
                continue
            a, b = k.head(n), k.tail(n)
            vs = capsule(bm, a, b, r, r * 0.9, 8)
            if "." not in part:                          # a joint: a knob where the segment starts
                vs += sphere(bm, a, r * 1.55, 8)
            k.add(n, vs)
        for b in k.B:
            nm = b.name
            if nm.startswith("DEF-") and nm.endswith(f".{side}") and ("_index" in nm or "_middle" in nm
                                                                      or "_ring" in nm or "_pinky" in nm):
                a, t = b.head_local.copy(), b.tail_local.copy()
                vs = capsule(bm, a, t, 0.035, 0.03, 5)
                if nm.split(".")[1] == "03":             # the last joint carries a claw
                    d = (t - a).normalized()
                    vs += cone(bm, t, t + d * 0.12 + Vector((0, 0, -0.04)), 0.03)
                k.add(nm, vs)

    # the skull (the scan) and a lower jaw that can chomp
    skull_verts = _skull(bm, k, skull_path)
    k.add("DEF-spine.011", skull_verts)
    hinge = Vector((0, 0.25, 4.85))
    jaw = []
    for s in (1, -1):
        jaw += chain(bm, [Vector((s * 0.42, 0.32, 5.15)), Vector((s * 0.45, 0.25, 4.82)),
                          Vector((s * 0.33, -0.45, 4.62)), Vector((s * 0.14, -1.05, 4.6))], 0.09, 0.07)
        for i in range(6):                              # lower teeth
            p = Vector((s * (0.36 - i * 0.04), -0.35 - i * 0.13, 4.7))
            jaw += cone(bm, p, p + Vector((0, 0, 0.16 if i < 5 else 0.26)), 0.035 if i < 5 else 0.05)
    jaw += capsule(bm, Vector((0.14, -1.08, 4.6)), Vector((-0.14, -1.08, 4.6)), 0.08, 0.08, 6)
    k.add("DEF-jaw", jaw)
    # soul-fire in the eye sockets
    for s in (1, -1):
        k.add("DEF-spine.011", sphere(bm, Vector((s * 0.33, -0.3, 5.7)), 0.13, 10), soul=True)
    return k.finish("harry_skeleton", coll), hinge


def _skull(bm, k, path, faces=33000):
    """import the scan, thin it out, turn it to face -Y with its top up, size it to the hound's head,
    and copy it into the skeleton's bmesh."""
    before = set(bpy.data.objects)
    bpy.ops.wm.stl_import(filepath=path)
    sk = [o for o in bpy.data.objects if o not in before][0]
    if len(sk.data.polygons) > faces:
        d = sk.modifiers.new("dec", "DECIMATE"); d.ratio = faces / len(sk.data.polygons)
        bpy.context.view_layer.objects.active = sk
        bpy.ops.object.modifier_apply(modifier="dec")
    me = sk.data
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    # the scan: snout toward -Z, top toward +Y, width along X -> the hound: snout -Y, top +Z
    R = np.array([[-1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]])
    p = co @ R.T
    length = p[:, 1].max() - p[:, 1].min()
    s = 2.25 / length
    p *= s
    nose = Vector((0.0, -1.42, 5.0))                     # where the hound's nose and brow are
    p[:, 1] += nose.y - p[:, 1].min()
    p[:, 2] += 6.05 - p[:, 2].max()
    p[:, 0] -= p[:, 0].mean()
    tmp = bmesh.new(); tmp.from_mesh(me)
    for v, q in zip(tmp.verts, p):
        v.co = Vector(q)
    tmp.verts.ensure_lookup_table()
    m = bpy.data.meshes.new("_skull"); tmp.to_mesh(m); tmp.free()
    # new verts can land in slots freed earlier (cones merge their tips), so find them by difference,
    # not as "everything after the old count"
    old = set(bm.verts)
    bm.from_mesh(m)
    bpy.data.objects.remove(sk); bpy.data.meshes.remove(m)
    return [v for v in bm.verts if v not in old]
