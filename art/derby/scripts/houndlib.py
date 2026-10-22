"""Hellhound Derby art pipeline: load the Hell Hound, light it, pose it procedurally, render frames.

Runs inside Blender (`blender -b -Y -P <script> -- ...`); see ../README.md.
"""
import bpy, math
import numpy as np
from mathutils import Matrix, Vector

TAIL = ["DEF-spine.003", "DEF-spine.002", "DEF-spine.001", "DEF-spine"]   # base -> tip (bones point tip -> body)
JAW_HINGE = Vector((0, 0.25, 4.85))


def load(src):
    """Import the Hell Hound FBX into an empty scene; returns the armature."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=src)
    rig = bpy.data.objects["rig"]
    # Poser moves DEF bones freely; a connected bone ignores its location channel and stays pinned
    # to a parent (often an MCH/ORG helper) that is never posed. Disconnecting keeps the rest pose.
    vl = bpy.context.view_layer
    vl.objects.active = rig; rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for eb in rig.data.edit_bones:
        eb.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    for m in bpy.data.materials:
        for n in m.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                if m.name == "Material.002":            # body: lava cracks glow
                    n.inputs["Emission Strength"].default_value = 4.0
                if m.name == "Material.004":            # eyes
                    n.inputs["Emission Color"].default_value = (1.0, 0.25, 0.02, 1)
                    n.inputs["Emission Strength"].default_value = 6.0
    return rig


def setup_render(w, h, rim=6.0):
    sc = bpy.context.scene
    wd = bpy.data.worlds.new("w"); sc.world = wd; wd.use_nodes = True
    wd.node_tree.nodes["Background"].inputs[0].default_value = (0.012, 0.006, 0.008, 1)
    sc.render.engine = "BLENDER_EEVEE"; sc.eevee.taa_render_samples = 32
    sc.render.film_transparent = True
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.image_settings.file_format = "PNG"; sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "AgX"; sc.view_settings.look = "AgX - Medium High Contrast"
    cam_d = bpy.data.cameras.new("cam"); cam_d.type = "ORTHO"
    cam = bpy.data.objects.new("cam", cam_d); sc.collection.objects.link(cam); sc.camera = cam
    cam.rotation_euler = (math.radians(90), 0, math.radians(-90))   # at -X looking +X: the dog runs left to right

    def light(name, energy, color, travel, angle=2.0):
        """a sun whose light travels along `travel` (the camera is at -X, the dog faces -Y)."""
        d = bpy.data.lights.new(name, "SUN"); d.energy = energy; d.color = color
        d.angle = math.radians(angle)
        o = bpy.data.objects.new(name, d); sc.collection.objects.link(o)
        o.rotation_mode = "QUATERNION"
        o.rotation_quaternion = Vector(travel).normalized().to_track_quat("-Z", "Y")
    light("key", 2.6, (1.0, 0.93, 0.86), (0.55, 0.35, -0.75))     # camera side, above, from the head end
    light("rim", rim, (1.0, 0.32, 0.07), (-0.75, -0.15, -0.45))   # from behind the dog toward the camera
    light("lava", 0.9, (1.0, 0.25, 0.05), (0.35, 0.0, 0.95))      # warm bounce from the ground below
    light("fill", 0.35, (0.5, 0.45, 1.0), (0.9, -0.2, 0.1))       # cold, low, camera side
    return cam


def frame_camera(cam, y0, y1, z0, z1, w, h, pad=1.15):
    cam.location = (-30, (y0 + y1) / 2, (z0 + z1) / 2)
    cam.data.ortho_scale = max(y1 - y0, (z1 - z0) * w / h) * pad


class Poser:
    """Pose DEF bones from planar joint angles: rotations about the armature X axis.
    Positive = toward the tail for a hanging leg, tip down for a bone pointing at the head."""

    def __init__(self, rig):
        self.rig = rig
        self.extra = {}       # {DEF bone: fixed rest-space transform before its own turn} (Terry's extra necks)
        B = rig.data.bones
        self.rest = {b.name: b.matrix_local.copy() for b in B}
        self.order = []

        def visit(b):
            self.order.append(b.name)
            for c in b.children:
                visit(c)
        for b in B:
            if b.parent is None:
                visit(b)
        self.vpar, self.pivot = {}, {}
        for b in B:
            if not b.name.startswith("DEF-"):
                continue
            self.vpar[b.name] = self._vparent(b)
            self.pivot[b.name] = b.head_local.copy()
        # the tail chain hangs off the hips, reversed, rotating about each bone's body end
        prev = "DEF-spine.004"
        for n in TAIL:
            self.vpar[n] = prev; self.pivot[n] = B[n].tail_local.copy(); prev = n
        # the rig hangs the shoulders (and so the front legs) on DEF-spine.008, the base of the neck:
        # they belong to the chest, or lowering the head would swing the front legs down with it
        for n in ("DEF-shoulder.L", "DEF-shoulder.R", "DEF-breast.L", "DEF-breast.R"):
            if n in self.vpar:
                self.vpar[n] = "DEF-spine.007"
        self.dorder, seen = [], set()

        def add(n):
            if n in seen:
                return
            p = self.vpar[n]
            if p:
                add(p)
            seen.add(n); self.dorder.append(n)
        for n in self.vpar:
            add(n)

    def _vparent(self, b):
        """the DEF bone this one moves with: its nearest DEF ancestor, an ORG-x ancestor standing for DEF-x."""
        B = self.rig.data.bones; p = b.parent
        while p is not None:
            if p.name.startswith("DEF-"):
                return p.name
            if p.name.startswith("ORG-"):
                d = "DEF-" + p.name[4:]
                if d in B and d != b.name:
                    return d
            p = p.parent
        return None

    @staticmethod
    def rot_about(p, deg):
        return Matrix.Translation(p) @ Matrix.Rotation(math.radians(deg), 4, "X") @ Matrix.Translation(-p)

    def _step(self, base, n, a, pivots):
        """base transform of a bone's parent -> the bone's own (rest-space) transform."""
        if n in self.extra:
            base = base @ self.extra[n]
        return base @ self.rot_about(pivots.get(n, self.pivot[n]), a) if a else base

    def point(self, angles, root, pivots, bone, end="tail"):
        """where one bone's end lands for these angles (armature space), without posing the rig."""
        chain, n = [], bone
        while n:
            chain.append(n); n = self.vpar[n]
        D = root if root is not None else Matrix.Identity(4)
        pivots = pivots or {}
        for n in reversed(chain):
            D = self._step(D, n, angles.get(n, 0.0), pivots)
        b = self.rig.data.bones[bone]
        return D @ (b.tail_local if end == "tail" else b.head_local)

    def apply(self, angles, root=None, pivots=None):
        """angles: {DEF bone: degrees}; root: rest-space transform for the whole body;
        pivots: {DEF bone: rest-space point} overriding the joint a bone turns about."""
        root = root if root is not None else Matrix.Identity(4)
        pivots = pivots or {}
        D = {}
        for n in self.dorder:
            p = self.vpar[n]
            D[n] = self._step(D[p] if p else root, n, angles.get(n, 0.0), pivots)
        pose = {}
        rig = self.rig
        for n in self.order:
            b = rig.data.bones[n]; pb = rig.pose.bones[n]
            par = b.parent.name if b.parent else None
            chain = (pose[par] @ self.rest[par].inverted() @ self.rest[n]) if par else self.rest[n]
            if n in D:
                want = D[n] @ self.rest[n]
                basis = chain.inverted() @ want
                pose[n] = want
            else:
                basis = Matrix.Identity(4); pose[n] = chain
            loc, q, s = basis.decompose()
            pb.rotation_mode = "QUATERNION"
            pb.location, pb.rotation_quaternion, pb.scale = loc, q, s
        bpy.context.view_layer.update()


def face_groups(P):
    """jaw bones (turn about the hinge) and ear bones (each ear turns as one piece about its base).
    Rigify ear bones form a loop and some hang off the head, so they are all re-hung on the head."""
    B = P.rig.data.bones
    jaw = sorted(n for n in P.vpar if any(k in n for k in ("jaw", "chin", "lip.B", "tongue", "teeth.B")))
    ears, pivots = {}, {n: JAW_HINGE for n in jaw}
    for side in ("L", "R"):
        for n in [n for n in P.vpar if n.startswith(f"DEF-ear.{side}")]:
            sfx = n[n.index("~"):] if "~" in n else ""       # an extra head's copy
            P.vpar[n] = "DEF-spine.011" + sfx
            ears[n] = side; pivots[n] = B[f"DEF-ear.{side}{sfx}"].head_local.copy()
    return jaw, ears, pivots


def _head_bones(P):
    """the DEF bones of the head and upper neck: everything that moves with DEF-spine.011, and .009/.010."""
    out = {"DEF-spine.009", "DEF-spine.010"}
    for n in P.vpar:
        m = n
        while m and m != "DEF-spine.011":
            m = P.vpar[m]
        if m:
            out.add(n)
    return out


def add_heads(rig, heads):
    """Give the hound more heads (Three-Heads Terry): copy the head and upper-neck bones once per
    entry of `heads` (bone names get the entry's "sfx", e.g. "~2"), and copy the mesh of the head and
    neck and the eyes onto them. The copies sit exactly on the original at rest; `head_offsets` moves
    them apart when posing. Call before making the Poser."""
    import bmesh
    P0 = Poser(rig)
    hb = _head_bones(P0)
    vl = bpy.context.view_layer
    vl.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    eb = rig.data.edit_bones
    for hd in heads:
        s = hd["sfx"]
        for n in hb:
            src, c = eb[n], eb.new(n + s)
            c.head, c.tail, c.roll = src.head.copy(), src.tail.copy(), src.roll
            c.use_deform, c.use_connect = True, False
        for n in hb:                                   # a DEF-only hierarchy, hung where the original's was
            vp = P0.vpar[n]
            eb[n + s].parent = eb[vp + s] if vp in hb else eb[vp]
    bpy.ops.object.mode_set(mode="OBJECT")
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.name != "collar"]
    for o in meshes:
        groups = {g.index for g in o.vertex_groups if g.name in hb}
        keep = set()
        for v in o.data.vertices:
            if sum(g.weight for g in v.groups if g.group in groups) >= 0.15:
                keep.add(v.index)
        if not keep:
            continue
        for hd in heads:
            new = o.copy(); new.data = o.data.copy(); new.name = o.name + hd["sfx"]
            o.users_collection[0].objects.link(new)
            bm = bmesh.new(); bm.from_mesh(new.data); bm.verts.ensure_lookup_table()
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.index not in keep], context="VERTS")
            bm.to_mesh(new.data); bm.free()
            for g in new.vertex_groups:
                if g.name in hb:
                    g.name = g.name + hd["sfx"]
    return hb


def tint(spec):
    """Re-colour the hound's skin in the render itself (where a hue shift of the whole sprite can't, e.g.
    Terry: a red body with black stripes, each head its own colour).

    spec: {object-name suffix: {...}}; "" is the body (the original mesh), "~2" an extra head's copy.
      skin:   [r, g, b]  the base colour of the coat (the model's coat is near black)
      head:   [r, g, b]  the colour this head turns, shading across it from the back of the skull
                         (still `skin`) to the muzzle
      stripe: [r, g, b]  what the lava cracks become (default: unchanged, glowing)
      glow:   factor on the cracks' glow (0 = none)
    The coat keeps the texture's light and dark (saddle, legs, fur), with ambient occlusion in the
    creases, a fine fur bump and a soft sheen, so a bright coat still looks muscled, not flat."""
    back, nose = Vector((0, 0.55, 5.3)), Vector((0, -1.36, 5.0))      # the skull, rest pose, armature space
    axis = nose - back
    for o in [o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith("cerber remesh.001")]:
        sfx = o.name[len("cerber remesh.001"):]
        t = spec.get(sfx)
        if t is None:
            continue
        if t.get("head"):
            M = o.matrix_world
            att = o.data.attributes.get("head_mix") or o.data.attributes.new("head_mix", "FLOAT", "POINT")
            att.data.foreach_set("value", [_ss(1.3 * (M @ v.co - back).dot(axis) / axis.length_squared)
                                           for v in o.data.vertices])
        mat = o.data.materials[0].copy()
        o.data.materials[0] = mat
        nt, links = mat.node_tree, mat.node_tree.links
        bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
        base_in = next(l for l in links if l.to_socket == bsdf.inputs["Base Color"]).from_socket
        emit_in = next(l for l in links if l.to_socket == bsdf.inputs["Emission Color"]).from_socket

        def rgb(c):
            return (*c[:3], 1.0)

        def mix(a, b, fac, blend="MIX"):
            m = nt.nodes.new("ShaderNodeMix"); m.data_type = "RGBA"; m.blend_type = blend
            for sock, v in ((m.inputs["Factor"], fac), (m.inputs[6], a), (m.inputs[7], b)):
                if isinstance(v, (int, float)):
                    sock.default_value = v
                elif isinstance(v, tuple):
                    sock.default_value = v
                else:
                    links.new(v, sock)
            return m.outputs[2]

        # where the cracks are: the glow texture's brightness, sharpened
        bw = nt.nodes.new("ShaderNodeRGBToBW"); links.new(emit_in, bw.inputs["Color"])
        ramp = nt.nodes.new("ShaderNodeValToRGB"); links.new(bw.outputs["Val"], ramp.inputs["Fac"])
        ramp.color_ramp.elements[0].position, ramp.color_ramp.elements[1].position = 0.003, 0.025
        crack = ramp.outputs["Color"]
        if t.get("skin"):
            coat = rgb(t["skin"])
            if t.get("head"):                              # shading across the head toward the muzzle
                attr = nt.nodes.new("ShaderNodeAttribute"); attr.attribute_name = "head_mix"
                coat = mix(coat, rgb(t["head"]), attr.outputs["Fac"])
            # the fur's light and dark: the dark texture's brightness (saddle, legs, strands) stretched
            # wide round 1, then ambient occlusion deepening the creases between the muscles
            lum = nt.nodes.new("ShaderNodeRGBToBW"); links.new(base_in, lum.inputs["Color"])
            shade = nt.nodes.new("ShaderNodeMapRange")
            shade.inputs["From Min"].default_value, shade.inputs["From Max"].default_value = 0.0, 0.04
            shade.inputs["To Min"].default_value, shade.inputs["To Max"].default_value = 0.28, 1.35
            links.new(lum.outputs["Val"], shade.inputs["Value"])
            coat = mix(coat, (1, 1, 1, 1), 1.0, "MULTIPLY")
            links.new(shade.outputs["Result"], coat.node.inputs[7])
            ao = nt.nodes.new("ShaderNodeAmbientOcclusion"); ao.inputs["Distance"].default_value = 0.6
            occl = nt.nodes.new("ShaderNodeMapRange")
            occl.inputs["To Min"].default_value, occl.inputs["To Max"].default_value = 0.45, 1.0
            links.new(ao.outputs["AO"], occl.inputs["Value"])
            coat = mix(coat, (1, 1, 1, 1), 1.0, "MULTIPLY")
            links.new(occl.outputs["Result"], coat.node.inputs[7])
            skin = mix(coat, rgb(t.get("stripe", (0.02, 0.01, 0.01))), crack)
            links.new(skin, bsdf.inputs["Base Color"])
            bump = nt.nodes.new("ShaderNodeBump")             # a fine fur grain from the same texture
            bump.inputs["Strength"].default_value, bump.inputs["Distance"].default_value = 0.35, 0.02
            links.new(lum.outputs["Val"], bump.inputs["Height"])
            links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
            bsdf.inputs["Sheen Weight"].default_value = 0.6       # soft light along the edges of the coat
            bsdf.inputs["Sheen Roughness"].default_value = 0.45
            bsdf.inputs["Sheen Tint"].default_value = (1.0, 0.75, 0.65, 1.0)
        bsdf.inputs["Emission Strength"].default_value *= t.get("glow", 1.0)


def head_offsets(P, heads):
    """where each extra head sits: a fixed shift sideways and a turn about its neck base."""
    for hd in heads:
        n = "DEF-spine.009" + hd["sfx"]
        p = P.pivot[n]
        P.extra[n] = (Matrix.Translation((hd.get("x", 0.0), hd.get("y", 0.0), hd.get("z", 0.0)))
                      @ Matrix.Translation(p) @ Matrix.Rotation(math.radians(hd.get("yaw", 0.0)), 4, "Z")
                      @ Matrix.Translation(-p))


def _ss(x):
    x = min(max(x, 0.0), 1.0); return x * x * (3 - 2 * x)


def _leg(p, s, land, reach):
    """one leg at its own phase p (0 = touchdown): returns hip angle, fold (0..1, knees and wrists
    tucked in the air), drive (0..1, the leg straight and pushing) and stance progress (0..1, or -1 in
    the air).

    On the ground the paw sweeps from `land[0]` (in front of the hip) to `land[1]` (behind it); in the
    air the leg carries on back to `reach[1]`, swings through to `reach[0]` and pulls back to land, so
    the big reaches happen in flight and a planted paw stays under the body."""
    p %= 1.0
    td, lo = land
    fwd, back = reach
    if p < s:
        st = p / s
        return td + (lo - td) * st, 0.0, _ss((st - 0.45) / 0.55), st
    u = (p - s) / (1 - s)
    if u < 0.22:                                   # still driving back after lift-off
        k = _ss(u / 0.22)
        hip = lo + (back - lo) * k
    elif u < 0.84:                                 # the swing through
        hip = back - (back + fwd) * _ss((u - 0.22) / 0.62)
    else:                                          # reaching, then pulling back to land
        hip = -fwd + (fwd + td) * _ss((u - 0.84) / 0.16)
    fold = math.sin(math.pi * min(max((u - 0.12) / 0.66, 0.0), 1.0))
    drive = max(0.0, 1.0 - u / 0.22)
    return hip, fold, drive, -1.0


# The default gallop: a wolf at full run. A hound's profile (hounds.json) overrides any of these.
GALLOP = dict(
    neck=(42, 18, 8, -62),            # neck base, neck, neck top, head (deg, + = tip down): one line nose to tail
    ears=(-70, -70),                  # L, R (deg, - = laid back)
    tail_lift=-1, tail_wag=4,         # tail base angle, wave amplitude
    drop=0.3, flight=0.3, flex=6,     # body carried lower than standing, height of the leap, back flex
    pitch=2, lean=5,                  # nose up while the front legs reach, down as they land; constant nose-down lean (deg)
    head_bob=3,
    hind=(0.00, 0.14), front=(0.44, 0.58),          # touchdown phase of L/R hind, R/L front
    stance=(0.30, 0.28),                            # share of the cycle a hind / front paw is down
    hind_land=(-26, 34), front_land=(-30, 30),      # hip angle at touchdown and lift-off (the planted sweep)
    hind_reach=(48, 72), front_reach=(66, 55),      # (forward, back) hip swing in the air, degrees
    limp=None,                        # e.g. ["R", 0.55]: that hind leg reaches only 55% as far
    jaw=(9, 3),                       # open, panting amplitude
    scale=1.0,
)


def profile(over=None):
    g = dict(GALLOP)
    for k, v in (over or {}).items():
        g[k] = tuple(v) if isinstance(v, list) and k != "limp" else v
    if not isinstance(g["ears"], (tuple, list)):
        g["ears"] = (g["ears"], g["ears"])
    return g


def _leg_setup(g, paw):
    """stance share, (touchdown, lift-off) and (forward, back) reach for one leg, and its limp factor."""
    if paw.endswith("H"):
        limp_side, limp_k = (g["limp"] or [None, 1.0])
        k = limp_k if paw[0] == limp_side else 1.0
        s = g["stance"][0] * (1.0 if k == 1.0 else 0.75)
        return s, tuple(v * k for v in g["hind_land"]), tuple(v * k for v in g["hind_reach"]), k
    return g["stance"][1], tuple(g["front_land"]), tuple(g["front_reach"]), 1.0


def gallop(ph, jaw, ears, g=GALLOP):
    """joint angles, body transform and planted paws for phase ph (0..1) of a rotary gallop: the
    body held in one long line from nose to tail, legs reaching far forward and driving far back in
    the air, every paw landing on its own beat. The body height comes later, from the planted paws
    (see `cycle`)."""
    T = 2 * math.pi
    a, down = {}, {}
    nod = 0.0
    for side, off in zip(("L", "R"), g["hind"]):
        s, land, reach, k = _leg_setup(g, f"{side}H")
        hip, fold, drive, st = _leg(ph - off, s, land, reach)
        drive *= k
        down[f"{side}H"] = st
        if k != 1.0 and st >= 0:
            nod = math.sin(math.pi * st)           # the head dips when the bad leg takes the weight
        # in the air stifle and hock fold to lift the paw; on the ground a little give, then the leg
        # straightens and the hock opens as it drives the body forward
        give = 10 * math.sin(math.pi * st) if st >= 0 else 0.0
        a[f"DEF-thigh.{side}"] = hip
        a[f"DEF-shin.{side}"] = 55 * fold ** 1.2 + give - 22 * drive
        a[f"DEF-foot.{side}"] = -55 * fold - 1.2 * give + 34 * drive
        a[f"DEF-toe.{side}"] = -30 * fold + 20 * drive
    for side, off in zip(("R", "L"), g["front"]):
        s, land, reach, _ = _leg_setup(g, f"{side}F")
        hip, fold, drive, st = _leg(ph - off, s, land, reach)
        down[f"{side}F"] = st
        # the forelimb folds at the wrist in the air and arrives straight, reaching far ahead
        a[f"DEF-front_thigh.{side}"] = hip
        a[f"DEF-front_shin.{side}"] = -25 * fold - 8 * drive
        a[f"DEF-front_foot.{side}"] = 90 * fold ** 0.9 + 18 * drive
        a[f"DEF-front_toe.{side}"] = 40 * fold + 25 * drive
    flex = g["flex"] * math.cos(T * (ph + 0.05))          # + = back arched (gathered)
    for n in ("DEF-spine.005", "DEF-spine.006", "DEF-spine.007"):
        a[n] = flex
    nk = g["neck"]
    a["DEF-spine.008"] = nk[0] - flex * 0.6
    a["DEF-spine.009"] = nk[1] - flex * 0.6
    a["DEF-spine.010"] = nk[2]
    a["DEF-spine.011"] = nk[3] - g["head_bob"] * math.sin(T * ph) + 10 * nod
    for k, n in enumerate(TAIL):
        a[n] = (g["tail_lift"] if k == 0 else 1) + g["tail_wag"] * math.sin(T * ph - k * 0.9)
    for n, side in ears.items():
        a[n] = g["ears"][0 if side == "L" else 1]
    phase = {hd["sfx"]: hd.get("phase", 0.0) for hd in g.get("heads") or []}
    for n in jaw:                                         # panting: the lower jaw drops about its hinge
        p2 = ph + phase.get(n[n.index("~"):], 0.0) if "~" in n else ph
        a[n] = g["jaw"][0] + g["jaw"][1] * math.sin(T * 2 * p2)
    for hd in g.get("heads") or []:                       # extra heads: their own carriage, out of step
        s, p2 = hd["sfx"], ph + hd.get("phase", 0.0)
        a["DEF-spine.009" + s] = a["DEF-spine.009"] + hd.get("pitch", 0.0)
        a["DEF-spine.010" + s] = a["DEF-spine.010"]
        a["DEF-spine.011" + s] = nk[3] - g["head_bob"] * math.sin(T * p2) - 0.6 * hd.get("pitch", 0.0)
    # the hind legs' drive tips the chest up while the front legs reach; it comes down onto them
    pitch = g["lean"] - g["pitch"] * math.sin(T * (ph - 0.05)) - flex * 0.5
    root = Matrix.Translation((0, 0, -0.15 * nod)) @ Poser.rot_about(Vector((0, 3.0, 3.0)), pitch)
    return a, root, down


PAW_BONES = {"LH": "DEF-toe.L", "RH": "DEF-toe.R", "LF": "DEF-front_toe.L", "RF": "DEF-front_toe.R"}


HIP = {"H": "DEF-thigh.{}", "F": "DEF-front_thigh.{}"}
# a knee-and-wrist bend that shortens a leg while keeping the paw roughly under itself (degrees per unit)
BEND = {"H": {"DEF-shin.{}": 1.0, "DEF-foot.{}": -1.0, "DEF-toe.{}": 0.3},
        "F": {"DEF-front_shin.{}": 1.0, "DEF-front_foot.{}": -1.2, "DEF-front_toe.{}": 0.5}}
# the way a leg tucks up in the air (the gait's own fold), for lifting a swinging paw clear
FOLD = {"H": {"DEF-shin.{}": 1.0, "DEF-foot.{}": -1.0, "DEF-toe.{}": -0.55},
        "F": {"DEF-front_shin.{}": -0.28, "DEF-front_foot.{}": 1.0, "DEF-front_toe.{}": 0.44}}
CLEAR = 0.35          # how far a swinging paw stays above the ground


def _leap_centre(g, jaw, ears, samples=200):
    """the middle of the longest stretch of the loop with no paw on the ground."""
    air = [all(st < 0 for st in gallop(i / samples, jaw, ears, g)[2].values()) for i in range(samples)]
    if all(air) or not any(air):
        return 0.0
    best, start = (0, 0), None
    for i in range(2 * samples):
        if air[i % samples]:
            start = i if start is None else start
            if i - start + 1 > best[1]:
                best = (start, i - start + 1)
        else:
            start = None
    return ((best[0] + best[1] / 2) / samples) % 1.0


def cycle(P, n, jaw, ears, pivots, g=GALLOP):
    """the n poses of one gallop loop, with planted paws standing on the ground.

    The body rides a smooth height (a little lower than standing, with a leap in the longest flight).
    Every planted paw is solved onto the ground with two unknowns, the hip angle and a knee/wrist bend,
    at a spot that slides back at one shared ground speed; a swinging paw that would scrape the ground
    folds further. Returns [(angles, root, stance)], stance = {paw: progress 0..1, or -1 in the air}."""
    rig = P.rig
    rest = {k: rig.data.bones[b].tail_local.copy() for k, b in PAW_BONES.items()}
    leap = _leap_centre(g, jaw, ears)

    def lift(ph):
        d = abs(ph - leap); d = min(d, 1 - d)
        return -g["drop"] + g["flight"] * math.exp(-(d / 0.12) ** 2)

    def leg_names(paw):
        side, kind = paw[0], paw[1]
        return HIP[kind].format(side), {k.format(side): w for k, w in BEND[kind].items()}

    # the ground speed: how fast a paw sweeps back under a resting body, averaged over the legs
    speed, mid = [], {}
    for paw, bone in PAW_BONES.items():
        s, land, _, _ = _leg_setup(g, paw)
        hip, _ = leg_names(paw)
        y0 = P.point({hip: land[0]}, None, pivots, bone).y
        y1 = P.point({hip: land[1]}, None, pivots, bone).y
        mid[paw] = (y0 + y1) / 2
        speed.append((y1 - y0) / s)
    v = min(speed)                                  # armature units per loop: what every leg can keep up with

    def solve(a, root, paw, ty, tz):
        hip, bend = leg_names(paw)
        bone = PAW_BONES[paw]
        h0 = a[hip]

        def at(h, c):
            b = dict(a); b[hip] = h
            for name, w in bend.items():
                b[name] = a.get(name, 0.0) + w * c
            p = P.point(b, root, pivots, bone)
            return p.y, p.z
        h, c = h0, 0.0
        for _ in range(10):
            y, z = at(h, c)
            ey, ez = ty - y, tz - z
            if abs(ey) + abs(ez) < 2e-3:
                break
            y1, z1 = at(h + 1, c); y2, z2 = at(h, c + 1)
            j00, j01, j10, j11 = y1 - y, y2 - y, z1 - z, z2 - z
            det = j00 * j11 - j01 * j10
            if abs(det) < 1e-7:
                break
            h += max(-12.0, min(12.0, (j11 * ey - j01 * ez) / det))
            c += max(-12.0, min(12.0, (-j10 * ey + j00 * ez) / det))
            c = max(-25.0, min(70.0, c))
        a[hip] = h
        for name, w in bend.items():
            a[name] = a.get(name, 0.0) + w * c

    def clear(a, root, paw):
        side, kind = paw[0], paw[1]
        hip, _ = leg_names(paw)
        bone = PAW_BONES[paw]
        floor = rest[paw].z + CLEAR
        if P.point(a, root, pivots, bone).z >= floor:
            return
        if a[hip] < -20:
            # reaching forward: folding would only hang the paw lower, so reach further and higher
            change, most = {hip: -1.0}, 40.0
        else:
            change, most = {k.format(side): w for k, w in FOLD[kind].items()}, 90.0
        lo, hi = 0.0, most
        for _ in range(12):                          # the least change that lifts the paw clear
            c = (lo + hi) / 2
            b = dict(a)
            for name, w in change.items():
                b[name] = a.get(name, 0.0) + w * c
            if P.point(b, root, pivots, bone).z >= floor:
                hi = c
            else:
                lo = c
        for name, w in change.items():
            a[name] = a.get(name, 0.0) + w * hi

    out = []
    for i in range(n):
        ph = i / n
        a, root, stance = gallop(ph, jaw, ears, g)
        root = Matrix.Translation((0, 0, lift(ph))) @ root
        for paw, st in stance.items():
            if st >= 0:
                s = _leg_setup(g, paw)[0]
                solve(a, root, paw, mid[paw] + v * s * (st - 0.5), rest[paw].z)
            else:
                clear(a, root, paw)
        out.append((a, root, stance))
    return out


def clear_action(rig):
    if rig.animation_data:
        rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)


def grid(files, cols, cw, ch, out, bg=(0.09, 0.08, 0.1)):
    """a contact sheet of rendered frames over a dark backdrop (for looking at, not for shipping)."""
    rows = math.ceil(len(files) / cols)
    W, H = cw * cols, ch * rows
    sheet = np.zeros((H, W, 4), dtype=np.float32); sheet[..., :3] = bg; sheet[..., 3] = 1
    for i, fp in enumerate(files):
        im = bpy.data.images.load(fp)
        px = np.array(im.pixels[:], dtype=np.float32).reshape(ch, cw, 4)
        r, c = divmod(i, cols); y0 = H - (r + 1) * ch; x0 = c * cw
        dst = sheet[y0:y0 + ch, x0:x0 + cw]; a = px[..., 3:4]
        dst[..., :3] = px[..., :3] * a + dst[..., :3] * (1 - a)
        bpy.data.images.remove(im)
    img = bpy.data.images.new("sheet", W, H, alpha=True); img.pixels[:] = sheet.ravel()
    img.filepath_raw = out; img.file_format = "PNG"; img.save()
