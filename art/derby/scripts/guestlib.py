"""Hellhound Derby guests: make any humanoid skeleton run.

The guest models come rigged every which way (Mixamo, game rips, MMD, 3ds Max Biped) and mostly with no run, so the
run is made here: the skeleton's joints are found by name (JOINTS, or the guest's own "joints"), and every frame of
one stride is posed by aiming bones, like the crowd (crowdlib.py): the feet follow a running path (two-bone IK, knees
forward), the arms pump against the legs, the body leans in and bounces. A model that comes with a run of its own
plays that instead (own_run). Runs inside Blender; see ../README.md.
"""
import bpy, math, re
from mathutils import Matrix, Quaternion, Vector

# canonical joint -> the bone name in each family (the right side is the left one mirrored by SIDES)
JOINTS = {
    "mixamo": {"Hips": "mixamorig:Hips", "Spine": "mixamorig:Spine", "Spine1": "mixamorig:Spine1", "Spine2": "mixamorig:Spine2",
               "Neck": "mixamorig:Neck", "Head": "mixamorig:Head", "LeftArm": "mixamorig:LeftArm",
               "LeftForeArm": "mixamorig:LeftForeArm", "LeftHand": "mixamorig:LeftHand", "LeftUpLeg": "mixamorig:LeftUpLeg",
               "LeftLeg": "mixamorig:LeftLeg", "LeftFoot": "mixamorig:LeftFoot", "LeftToe": "mixamorig:LeftToeBase"},
    "uma": {"Hips": "Hip", "Spine": "Waist", "Spine1": "Spine", "Spine2": "Chest", "Neck": "Neck", "Head": "Head",
            "LeftArm": "Arm_L", "LeftForeArm": "Elbow_L", "LeftHand": "Wrist_L", "LeftUpLeg": "Thigh_L", "LeftLeg": "Knee_L",
            "LeftFoot": "Ankle_offset_L", "LeftToe": "Toe_offset_L"},
    "mmd": {"Hips": "Hips", "Spine": "Spine", "Spine1": "Chest", "Spine2": "Upper Chest", "Neck": "Neck", "Head": "Head",
            "LeftArm": "Left arm", "LeftForeArm": "Left elbow", "LeftHand": "Left wrist", "LeftUpLeg": "Left leg",
            "LeftLeg": "Left knee", "LeftFoot": "Left ankle", "LeftToe": "Left toe"},
    "biped": {"Hips": "Bip001 Pelvis", "Spine": "Bip001 Spine", "Spine1": "Bip001 Spine1", "Spine2": "Bip001 Spine2",
              "Neck": "Bip001 Neck", "Head": "Bip001 Head", "LeftArm": "Bip001 L UpperArm", "LeftForeArm": "Bip001 L Forearm",
              "LeftHand": "Bip001 L Hand", "LeftUpLeg": "Bip001 L Thigh", "LeftLeg": "Bip001 L Calf", "LeftFoot": "Bip001 L Foot",
              "LeftToe": "Bip001 L Toe0"},
    "cc": {"Hips": "CC_Base_Hip", "Spine": "CC_Base_Waist", "Spine1": "CC_Base_Spine01", "Spine2": "CC_Base_Spine02",
           "Neck": "CC_Base_NeckTwist01", "Head": "CC_Base_Head", "LeftArm": "CC_Base_L_Upperarm",
           "LeftForeArm": "CC_Base_L_Forearm", "LeftHand": "CC_Base_L_Hand", "LeftUpLeg": "CC_Base_L_Thigh",
           "LeftLeg": "CC_Base_L_Calf", "LeftFoot": "CC_Base_L_Foot", "LeftToe": "CC_Base_L_ToeBase"},   # (Character Creator)
}
SIDES = [("Left", "Right"), ("_L", "_R"), (" L ", " R "), ("left", "right"), (".L", ".R")]
CHAIN = [("LeftUpLeg", "LeftLeg"), ("LeftLeg", "LeftFoot"), ("LeftFoot", "LeftToe"), ("LeftArm", "LeftForeArm"),
         ("LeftForeArm", "LeftHand"), ("Hips", "Spine"), ("Spine", "Spine1"), ("Spine1", "Spine2"), ("Spine2", "Neck"),
         ("Neck", "Head")]


def base(name):
    """a bone's name without the importer's _<index> suffix ("Thigh_L_059" -> "Thigh_L")."""
    return re.sub(r"_\d+$", "", name)


def right(n):
    for a, b in SIDES:
        if a in n:
            return n.replace(a, b)
    return n


def load(path, action=None, opts=None):
    """Import a guest (glTF, FBX) into an empty scene; returns its armature, posable, at rest. opts: the importer's
    options (a rig whose moves come out torn, or that stands in pieces: {"guess_original_bind_pose": false}). With
    action (the name of its own run), it keeps its rest as it came and plays that."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if path.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path, **(opts or {}))
    else:
        bpy.ops.import_scene.gltf(filepath=path, **(opts or {}))
    for o in list(bpy.data.objects):
        if o.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(o)
    for o in bpy.data.objects:
        o.animation_data_clear()
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    if action:
        for pb in arm.pose.bones:                      # (what the importer left posed: the action only keys its own)
            pb.location, pb.scale = (0, 0, 0), (1, 1, 1)
            pb.rotation_quaternion, pb.rotation_euler = (1, 0, 0, 0), (0, 0, 0)
        act = bpy.data.actions[action]
        ad = arm.animation_data_create()
        ad.action = act
        if not ad.action_slot:
            ad.action_slot = act.slots[0]
        bpy.context.view_layer.update()
        return arm
    bake_rest(arm)
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return arm


def reparent(arm, parents):
    """Hang bones on the bones they move with, for a rig that came flat (every deform bone on the root, moved by
    controls the export dropped): {bone: parent} (base names; a bone may be a pattern, its parent then taking the
    pattern's groups, "DEF-ARM\\.(L|R)": "DEF-SHOULDER.\\1"). The rest doesn't move, so call it at rest (load)."""
    names = {base(b.name): b.name for b in arm.data.bones}
    vl = bpy.context.view_layer
    for o in bpy.data.objects:
        o.select_set(o is arm)
    vl.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones
    for pat, par in parents.items():
        for b, full in names.items():
            m = re.fullmatch(pat, b)
            if m and names.get(m.expand(par)):
                eb[full].use_connect = False
                eb[full].parent = eb[names[m.expand(par)]]
    bpy.ops.object.mode_set(mode="OBJECT")
    vl.update()


def bake_rest(arm):
    """Make what you see the rest pose. Imported rigs often stand in a pose that isn't their rest (the rest can be
    anything, even with the bones at other scales), so: bake each skinned mesh as it stands, apply the pose as the
    rest, skin it again. (Shape keys go first: the faces' expressions; a skin can't be applied over them.)"""
    bpy.context.view_layer.update()
    skinned = []
    for o in [o for o in bpy.data.objects if o.type == "MESH"]:
        mods = [m for m in o.modifiers if m.type == "ARMATURE" and m.object == arm]
        if not mods:
            continue
        if o.data.shape_keys:
            o.shape_key_clear()
        with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o]):
            for m in mods:
                bpy.ops.object.modifier_apply(modifier=m.name)
        skinned.append(o)
    vl = bpy.context.view_layer
    for o in bpy.data.objects:
        o.select_set(o is arm)
    vl.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.armature_apply(selected=False)         # (every bone)
    bpy.ops.object.mode_set(mode="OBJECT")
    for o in skinned:
        m = o.modifiers.new("Armature", "ARMATURE"); m.object = arm
        if len(o.modifiers) > 1:                       # first, as it was
            with bpy.context.temp_override(object=o, active_object=o):
                bpy.ops.object.modifier_move_to_index(modifier=m.name, index=0)
    bpy.context.view_layer.update()


def fix_parts(arm, hide=(), attach=None, skin=None, squeeze=None):
    """For a model whose parts didn't all come skinned: hide the parts named by hide (their parent empty's name
    starts with one, or their own), pin parts to a bone (attach: {part: bone}, rigid), and give parts automatic
    weights from the bones listed (skin: {"parts": [...], "bones": [...], "upper": {"z": world height, "y_max": world y,
    "bones": [...]}}: only those bones deform them; above upper.z (and with y_max, at a y below it) only upper.bones
    do). squeeze: {"parts": [...], "by": share, "z": [from, to], "y": [from, to]}: narrows parts sideways (x, toward
    their middle) by that share, fading in from z[0] to z[1] (none below: the legs) and from y[0] to y[1] (none
    before y[0]: the rider). For a body under a cloak: seen only from the side, nothing shows, and the bulges of the
    run (the two are skinned differently) stay under the cloak. (Pushing it in along its normals folds it.)"""
    names = {base(b.name): b.name for b in arm.data.bones}
    part = lambda o, keys: o.type == "MESH" and any((o.parent and o.parent.name.startswith(k)) or o.name.startswith(k) for k in keys)
    vl = bpy.context.view_layer
    for o in bpy.data.objects:
        if part(o, hide):
            o.hide_render = True; o.hide_set(True)
    for key, bone in (attach or {}).items():
        for o in [o for o in bpy.data.objects if part(o, [key]) and not o.hide_render]:
            mw = o.matrix_world.copy()
            o.parent, o.parent_type, o.parent_bone = arm, "BONE", names[bone]
            o.matrix_world = mw
    if squeeze:                                        # (first: skinning reparents the parts)
        ramp = lambda a, lo, hi: min(1.0, max(0.0, (a - lo) / (hi - lo)))
        for o in [o for o in bpy.data.objects if part(o, squeeze["parts"]) and not o.hide_render]:
            mw, mi = o.matrix_world, o.matrix_world.inverted()
            ps = [mw @ v.co for v in o.data.vertices]
            mid = sum(p.x for p in ps) / len(ps)
            for v, p in zip(o.data.vertices, ps):
                k = squeeze["by"] * ramp(p.z, *squeeze["z"]) * ramp(p.y, *squeeze["y"])
                if k:
                    v.co = mi @ Vector((mid + (p.x - mid) * (1 - k), p.y, p.z))
    if skin:
        allowed = {names[n] for n in skin["bones"] if n in names}
        was = {b.name: b.use_deform for b in arm.data.bones}
        for b in arm.data.bones:
            b.use_deform = b.name in allowed
        for o in [o for o in bpy.data.objects if part(o, skin["parts"]) and not o.hide_render]:
            for x in bpy.data.objects:
                x.select_set(x in (o, arm))
            vl.objects.active = arm
            bpy.ops.object.parent_set(type="ARMATURE_AUTO", keep_transform=True)
            up = skin.get("upper")                     # above z, only these bones (a centaur's hands aren't its knees);
            if up:                                     # with y_max, only in front of it (its rump isn't its rider)
                keep = {names[n] for n in up["bones"] if n in names}
                to = o.vertex_groups.get(names[up["bones"][-1]]) or o.vertex_groups.new(name=names[up["bones"][-1]])
                groups = {g.index: g.name for g in o.vertex_groups}
                for v in o.data.vertices:
                    w = o.matrix_world @ v.co
                    if w.z > up["z"] and w.y < up.get("y_max", math.inf):
                        if not any(groups[g.group] in keep for g in v.groups):
                            to.add([v.index], 1.0, "REPLACE")  # (only lower bones had it: the last upper bone takes it)
                        for g in [g for g in v.groups if groups[g.group] not in keep]:
                            o.vertex_groups[groups[g.group]].remove([v.index])
        for b in arm.data.bones:
            b.use_deform = was[b.name]
    vl.update()


def find_joints(arm, own=None):
    """canonical -> bone name: the family whose names this skeleton has the most of, then the guest's own."""
    by_base = {}
    for b in arm.data.bones:
        by_base.setdefault(base(b.name), b.name)
    best, hits = None, -1
    for fam, m in JOINTS.items():
        n = sum(1 for v in m.values() if v in by_base)
        if n > hits:
            best, hits = fam, n
    out = {}
    for k, v in {**JOINTS[best], **(own or {})}.items():
        for kk, vv in ((k, v), (k.replace("Left", "Right"), right(v))):
            if vv in by_base:
                out[kk] = by_base[vv]
            elif vv in arm.data.bones:
                out[kk] = vv
    return best, out


def turn(a, b, deg):
    """the rotation that turns direction a toward direction b by deg degrees."""
    ax = a.cross(b)
    return Quaternion(ax.normalized(), math.radians(deg)) if ax.length > 1e-9 and deg else Quaternion()


class Rig:
    """A posable skeleton in its own frame: l (its left), f (forward), u (up), from its joints. Joints are aimed
    (head to the next joint's head, not the bone's tail: game rips have odd tails) or turned relative to their
    parent; the hips can move. Everything else follows its parent. level: up is the world's (a model with its
    own run, as it came: its rest needn't stand it up)."""

    def __init__(self, arm, joints, axes=None, level=False):
        self.arm, self.J = arm, joints
        mw = arm.matrix_world
        self.A = mw.to_3x3().normalized().to_quaternion()
        self.k = mw.to_scale()[0]
        self.bones = sorted(arm.data.bones, key=lambda b: len(b.parent_recursive))
        self.rest = {b.name: b.matrix_local.to_quaternion() for b in self.bones}
        self.head = {b.name: mw @ b.head_local for b in self.bones}
        self.dir = {b.name: (self.A @ (b.tail_local - b.head_local)).normalized() for b in self.bones}
        self.tdir = {}                                 # a loose part's way: to its next bone (not a swing bone's _Handle),
        for b in self.bones:                           # the last one on in its parent's line
            d = next((self.head[c.name] - self.head[b.name] for c in b.children
                      if "Handle" not in c.name and (self.head[c.name] - self.head[b.name]).length > 1e-6), None)
            self.tdir[b.name] = d.normalized() if d is not None else self.tdir[b.parent.name] if b.parent else self.dir[b.name]
        self.loose = {}
        for a, b in CHAIN + [(x.replace("Left", "Right"), y.replace("Left", "Right")) for x, y in CHAIN if "Left" in x]:
            if a in joints and b in joints:
                d = self.at(b) - self.at(a)
                if d.length > 1e-9:
                    self.dir[joints[a]] = d.normalized()
        if axes:                                       # (a centaur: given, with its ground and leg length)
            self.U, self.F, self.ground, self.leg = axes["U"], axes["F"], axes["ground"], axes["leg"]
            self.L = self.U.cross(self.F).normalized()
            return
        self.U = Vector((0, 0, 1)) if level else (self.at("Hips") - (self.at("LeftFoot") + self.at("RightFoot")) / 2).normalized()   # (a hunch doesn't tilt it)
        L = self.at("LeftArm") - self.at("RightArm") if "RightArm" in joints else self.at("LeftUpLeg") - self.at("RightUpLeg")
        self.L = (L - self.U * L.dot(self.U)).normalized()
        self.F = self.L.cross(self.U).normalized()
        self.ground = min(self.at(s + "Foot").dot(self.U) for s in ("Left", "Right"))
        self.leg = (self.at("LeftUpLeg") - self.at("LeftLeg")).length + (self.at("LeftLeg") - self.at("LeftFoot")).length

    def at(self, j):
        return self.head[self.J[j]]

    def v(self, l, f, u, s=1):
        return (self.L * (l * s) + self.F * f + self.U * u).normalized()

    def apply(self, spec, hips=Vector()):
        """spec: canonical joint -> ("aim", dir) | ("rel", Quaternion) | ("ik", foot target) | ("trail", toward, share,
        wave degrees: a loose part turns share of the way toward where the wind blows it, then waves about l);
        hips: a world offset."""
        J, D, pos = self.J, {}, {}
        sp = {J.get(k, k): v for k, v in spec.items()}  # (canonical joints, or bone names)
        for side in ("Left", "Right"):                 # an IK leg aims its knee too
            if (side + "UpLeg") in spec and spec[side + "UpLeg"][0] == "ik":
                sp[J[side + "UpLeg"]] = ("ik", side, spec[side + "UpLeg"][1])
        Ai = self.A.inverted()
        for b in self.bones:
            n = b.name
            p = b.parent.name if b.parent else None
            Dp = D[p] if p else Quaternion()
            pos[n] = (pos[p] + Dp @ (self.head[n] - self.head[p])) if p else self.head[n] + hips
            s = sp.get(n)
            if s is None:
                Db = Dp
            elif s[0] == "rel":
                Db = Dp @ s[1]
            elif s[0] == "aim":
                Db = (Dp @ self.dir[n]).rotation_difference(s[1]) @ Dp
            elif s[0] == "trail":
                d = Dp @ self.tdir[n]
                Db = Quaternion(self.L, math.radians(s[3])) @ turn(d, s[1], math.degrees(d.angle(s[1])) * s[2]) @ Dp
            else:                                     # ("ik", side, target): the thigh, then the shin
                side, T = s[1], s[2]
                l1 = (self.at(side + "UpLeg") - self.at(side + "Leg")).length
                l2 = (self.at(side + "Leg") - self.at(side + "Foot")).length
                H = pos[n]
                d = min((T - H).length, (l1 + l2) * 0.999); w = (T - H).normalized()
                a = (l1 * l1 - l2 * l2 + d * d) / (2 * d); h = math.sqrt(max(0.0, l1 * l1 - a * a))
                pole = (self.F - w * self.F.dot(w)).normalized()
                knee = H + w * a + pole * h
                Db = (Dp @ self.dir[n]).rotation_difference((knee - H).normalized()) @ Dp
                sp[J[side + "Leg"]] = ("aim", (T - knee).normalized())
            D[n] = Db
            Dpa, Dba, R = Ai @ Dp @ self.A, Ai @ Db @ self.A, self.rest[n]
            pb = self.arm.pose.bones[n]
            pb.rotation_quaternion = R.inverted() @ Dpa.inverted() @ Dba @ R
            pb.location = (R.inverted() @ (Ai @ hips) / self.k) if not p else Vector()
        bpy.context.view_layer.update()
        self.pos = pos

    def now(self, j):
        """where joint j is in the current pose (world)."""
        return self.pos[self.J[j]]


RUN = {"lean": 16, "stride": 0.95, "stance": 0.36, "lift": 0.42, "kick": 0.28, "bounce": 0.035, "drop": 0.06,
       "arm": 0.6, "elbow": 95, "nod": -8, "hip": 0, "arm_lean": 0}

# the loose parts that stream behind a runner (a game rip's swing bones: Sp_<where>_<what>): bone-name pattern ->
# [share (how far each bone turns toward the wind), lift (degrees above level behind it: the wind's way), wave (degrees)]
TRAIL = {r"^Sp_Hi_Tail": [0.45, 5, 9], r"^Sp_He_Hair[1-9]": [0.5, -20, 5],
         r"^Sp_(Ch|He|Hi)_(Jacket|Ribbon|Acc|Collar)": [0.2, -55, 4]}


def find_loose(rig, trail=None):
    """the bones of the loose parts (TRAIL, or the guest's own "trail"): {bone: [share, lift, wave, k]}, k its place
    down the chain (the wave runs down it). Never a joint, nor a swing bone's _Handle end."""
    out, joints = {}, set(rig.J.values())
    for pat, (share, lift, wave) in (TRAIL if trail is None else trail).items():
        for b in rig.bones:
            if b.name in joints or "Handle" in b.name or not re.search(pat, base(b.name)):
                continue
            k = sum(1 for p in b.parent_recursive if re.search(pat, base(p.name)))
            out[b.name] = [share, lift, wave, k]
    return out


def trail(rig, spec, ph):
    """the loose parts streaming back, waving twice a stride (with the bounce)."""
    for n, (share, lift, wave, k) in rig.loose.items():
        r = math.radians(lift)
        spec[n] = ("trail", -rig.F * math.cos(r) + rig.U * math.sin(r), share, wave * math.sin(4 * math.pi * ph - 0.9 * k))


def run_pose(rig, ph, P=None):
    """one frame of a run at phase ph (0..1, one full stride: the left foot lands at 0, the right at 0.5).
    P (RUN, the guest's "run"): lean degrees (the spine), hip (degrees the pelvis tips in too), stride (a foot's
    travel while down, in leg lengths), stance (the share of a stride a foot is down), lift / kick (how high and how
    far back the foot swings, leg lengths), bounce / drop (the hips, leg lengths), arm (swing), elbow (degrees
    bent), arm_lean (the share of the lean the arms follow), nod (the head, against the lean). The loose parts
    (rig.loose) stream behind. Returns {side: on the ground} for the feet."""
    P = {**RUN, **(P or {})}
    U, F, L, leg = rig.U, rig.F, rig.L, rig.leg
    S, beta = P["stride"] * leg, P["stance"]
    spec, down = {}, {}
    tilt = turn(U, F, (P["lean"] + P["hip"]) * P["arm_lean"])
    for side, s, off in (("Left", 1, 0.0), ("Right", -1, 0.5)):
        p = (ph + off) % 1.0
        foot = rig.at(side + "Foot")
        if p < beta:                                   # down: the foot slides back under the body
            f, u = S / 2 - S * p / beta, 0.0
            down[side] = True
        else:                                          # up: back and high (heel to the backside), then reaching forward
            q = (p - beta) / (1 - beta)
            f = -S / 2 + S * (1 - math.cos(math.pi * q)) / 2 - P["kick"] * leg * math.sin(math.pi * q) * (1 - q)
            u = P["lift"] * leg * math.sin(math.pi * q) ** 1.5
            down[side] = False
        T = foot + F * f + U * u                       # (the foot's own sideways place: its rest one)
        spec[side + "UpLeg"] = ("ik", T)
        sw = math.cos(2 * math.pi * (p - beta / 2))     # +1: this leg forward
        a = -sw * P["arm"]                               # the arm on this side swings against it
        up = rig.v(0.12, a * 1.1, -0.8, s)
        fore = turn(up, F, P["elbow"] + 15 * a) @ up   # bent from the upper arm: up to the face in front, by the hip behind
        spec[side + "Arm"] = ("aim", tilt @ up)
        spec[side + "ForeArm"] = ("aim", tilt @ fore)
    lean = turn(U, F, P["lean"] / 3)
    for j in ("Spine", "Spine1", "Spine2"):
        spec[j] = ("rel", lean)
    if P["hip"]:
        spec["Hips"] = ("rel", turn(U, F, P["hip"]))
    spec["Head"] = ("rel", turn(U, F, P["nod"]))
    trail(rig, spec, ph)
    b = -math.cos(4 * math.pi * (ph - beta / 2))       # lowest with a foot under the body, highest in the air
    hips = U * leg * (P["bounce"] * b - P["drop"])
    rig.apply(spec, hips)
    return down


def span(rig):
    """how low and how high it reaches (m, along its up), as it stands now."""
    dg = bpy.context.evaluated_depsgraph_get(); lo, hi = math.inf, -math.inf
    for o in bpy.data.objects:
        if o.type == "MESH" and o.visible_get():
            e = o.evaluated_get(dg); m = e.to_mesh(); mw = e.matrix_world
            vs = list(m.vertices)
            for v in vs[::max(1, len(vs) // 20000)]:
                u = (mw @ v.co).dot(rig.U)
                lo, hi = min(lo, u), max(hi, u)
            e.to_mesh_clear()
    return lo, hi


def play(rig, piv, t, at):
    """pose a guest that has its own run at frame t of it (fractional), its model moved to at (piv: the empty it all
    hangs on); notes where its joints are."""
    piv.location = at
    bpy.context.scene.frame_set(int(math.floor(t)), subframe=t - math.floor(t))
    mw = rig.arm.matrix_world
    rig.pos = {b.name: mw @ rig.arm.pose.bones[b.name].head for b in rig.bones}


def own_run(rig, piv, frames, n):
    """A guest with a run of its own (load's action): n frames of one stride, frames [from, to] of the action, held in
    place (a run that carries it along is moved back as far). Sets rig.ground (its lowest point over the stride),
    rig.mid (its hips, on average: the camera centres on them), rig.down ({side: [foot down in each frame]}: a foot
    within 8% of a leg of its lowest) and rig.travel (how far a
    stride carries it, m: as far as the run moved it, or as far back as its feet slide while down). Returns the frames,
    [(frame, where the model goes)], for play."""
    f0, f1 = frames
    base = piv.location.copy()
    ts = [f0 + (f1 - f0) * i / n for i in range(n + 1)]
    play(rig, piv, ts[0], base); h0 = rig.now("Hips").dot(rig.F)
    play(rig, piv, ts[n], base); drift = rig.now("Hips").dot(rig.F) - h0
    shots = [(ts[i], base - rig.F * drift * i / n) for i in range(n)]
    low, feet, rig.mid = math.inf, {"Left": [], "Right": []}, Vector()
    for t, at in shots:
        play(rig, piv, t, at)
        low = min(low, span(rig)[0])
        rig.mid += rig.now("Hips") / n
        for s, v in feet.items():
            p = rig.now(s + "Foot"); v.append((p.dot(rig.U), p.dot(rig.F)))
    rig.ground = low
    rig.down = {s: [u - min(x[0] for x in v) < 0.08 * rig.leg for u, _ in v] for s, v in feet.items()}
    back = [v[i][1] - v[(i + 1) % n][1] for s, v in feet.items() for i in range(n) if rig.down[s][i] and rig.down[s][(i + 1) % n]]
    rig.travel = abs(drift) if abs(drift) > 0.05 * rig.leg else sum(back) / len(back) * n if back else rig.leg
    return shots


def front_of_face(rig):
    """the front of its face as it stands now (a snout's tip, a beak's, the jaw): the frontmost point of the mesh its
    head moves (weighted at least half to the head bone or a bone on it); None if nothing is."""
    if not hasattr(rig, "face"):
        head = rig.arm.data.bones[rig.J["Head"]]
        names = {head.name} | {b.name for b in head.children_recursive}
        rig.face = {}
        for o in bpy.data.objects:
            if o.type == "MESH" and o.visible_get():
                gi = {g.index for g in o.vertex_groups if g.name in names}
                vs = [v.index for v in o.data.vertices if sum(g.weight for g in v.groups if g.group in gi) >= 0.5] if gi else []
                if vs:
                    rig.face[o.name] = vs[::max(1, len(vs) // 5000)]
    dg, best = bpy.context.evaluated_depsgraph_get(), None
    for name, vs in rig.face.items():
        e = bpy.data.objects[name].evaluated_get(dg); m = e.to_mesh(); mw = e.matrix_world
        for i in vs:
            p = mw @ m.vertices[i].co
            if best is None or p.dot(rig.F) > best.dot(rig.F):
                best = p
        e.to_mesh_clear()
    return best


def face_camera(cam, rig, w, h, ppm, z0=0.0, x0=0.5):
    """an orthographic camera on the guest's right, looking at its side (it runs to the right of the frame),
    ppm pixels a metre, its feet z0 of the frame height above the bottom, its hips x0 of the width from the left."""
    c = getattr(rig, "mid", None) or rig.at("Hips")
    gpt = c - rig.U * (c.dot(rig.U) - rig.ground)    # under the hips, on the ground
    centre = gpt + rig.U * ((0.5 - z0) * h / ppm) + rig.F * ((0.5 - x0) * w / ppm)
    cam.data.type = "ORTHO"; cam.data.ortho_scale = max(w, h) / ppm
    cam.location = centre - rig.L * 30
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = Matrix((rig.F, rig.U, -rig.L)).transposed().to_quaternion()   # x right = F, y up = U, looking along +L
    cam.data.clip_end = 100


# ---------------------------------------------------------------------------------------------- a centaur
GALLOP = {"stance": 0.36, "swing": 30, "carpus": 100, "lift": 35, "stifle": 45, "hock": 70, "fetlock": 30,
          "pitch": 5, "bounce": 0.05, "lean": -4, "torso": 0, "arm": 0,
          "lead": {"rear_l": 0.0, "rear_r": 0.09, "front_r": 0.40, "front_l": 0.49}}   # a rotary gallop's footfalls


def centaur(arm, legs, hips, head, torso=None, arms=()):
    """a centaur's rig: legs {"front_l": [bones from the shoulder down to the hoof], "front_r", "rear_l", "rear_r"}
    (base names), its body's root bone, its head, (optional) the root of its rider half and its arms (a bone each:
    they swing from there). Up is the world's, forward from the rear legs to the front."""
    names = {base(b.name): b.name for b in arm.data.bones}
    L = {k: [names[n] for n in v if n in names] for k, v in legs.items()}
    mw = arm.matrix_world
    at = lambda n: mw @ arm.data.bones[n].head_local
    tip = lambda n: mw @ arm.data.bones[n].tail_local
    U = Vector((0, 0, 1))
    F = (at(L["front_l"][0]) + at(L["front_r"][0]) - at(L["rear_l"][0]) - at(L["rear_r"][0]))
    F = (F - U * F.dot(U)).normalized()
    ground = min(tip(v[-1]).dot(U) for v in L.values())
    leg = sum((at(a) - at(b)).length for a, b in zip(L["front_l"], L["front_l"][1:]))
    J = {"Hips": names[hips], "Head": names[head], **({"Torso": names[torso]} if torso else {})}
    rig = Rig(arm, J, {"U": U, "F": F, "ground": ground, "leg": leg})
    rig.legs = L
    rig.arms = [names[n] for n in arms or () if n in names]
    return rig


def gallop_pose(rig, ph, P=None):
    """one frame of a gallop at phase ph. Each leg swings from the shoulder / hip (forward while it is up, back under
    the body while its hoof is down), the front knee folds up and the rear stifle and hock fold in its swing; the
    body pitches and bounces, the rider half leans in (torso degrees), the head against it (lean), the arms pump
    (arm degrees, against each other, once a stride). Returns {leg: hoof down}."""
    P = {**GALLOP, **(P or {})}
    beta, Lx, spec, down = P["stance"], rig.L, {}, {}
    rot = lambda deg: Quaternion(Lx, math.radians(deg))  # + swings a leg back, folds a joint backward
    for leg, bones in rig.legs.items():
        p = (ph - P["lead"][leg]) % 1.0
        if p < beta:
            s = p / beta; swing, fold = -P["swing"] + 2 * P["swing"] * s, 0.0
            down[leg] = True
        else:
            q = (p - beta) / (1 - beta); swing = P["swing"] - 2 * P["swing"] * (1 - math.cos(math.pi * q)) / 2
            fold = math.sin(math.pi * q)
            down[leg] = False
        spec[bones[0]] = ("rel", rot(swing))
        if leg.startswith("front"):                    # forearm lifts forward, the knee folds the cannon back
            if len(bones) > 1:
                spec[bones[1]] = ("rel", rot(-P["lift"] * fold))
            if len(bones) > 2:
                spec[bones[2]] = ("rel", rot(P["carpus"] * fold))
            if len(bones) > 3:
                spec[bones[3]] = ("rel", rot(P["fetlock"] * fold))
        else:                                          # the stifle comes forward, the hock folds back
            if len(bones) > 1:
                spec[bones[1]] = ("rel", rot(-P["stifle"] * fold))
            if len(bones) > 2:
                spec[bones[2]] = ("rel", rot(P["hock"] * fold))
            if len(bones) > 3:
                spec[bones[3]] = ("rel", rot(P["fetlock"] * fold * 0.6))
    w = 2 * math.pi * ph
    spec["Hips"] = ("rel", rot(P["pitch"] * math.sin(w)))
    if P["torso"] and "Torso" in rig.J:
        spec["Torso"] = ("rel", rot(P["torso"]))
    for i, bone in enumerate(rig.arms if P["arm"] else ()):
        spec[bone] = ("rel", rot(P["arm"] * math.sin(w + math.pi * i)))
    spec["Head"] = ("rel", rot(P["lean"]))
    trail(rig, spec, ph)
    hips = rig.U * rig.leg * P["bounce"] * math.sin(w + 1.2)
    rig.apply(spec, hips)
    return down
