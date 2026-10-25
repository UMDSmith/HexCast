"""Hellhound Derby guests: make any humanoid skeleton run.

The guest models come rigged every which way (Mixamo, game rips, MMD, 3ds Max Biped) and mostly with no run, so the
run is made here: the skeleton's joints are found by name (JOINTS, or the guest's own "joints"), and every frame of
one stride is posed by aiming bones, like the crowd (crowdlib.py): the feet follow a running path (two-bone IK, knees
forward), the arms pump against the legs, the body leans in and bounces. A model that comes with a run of its own
plays that instead (own_run). Runs inside Blender; see ../README.md.
"""
import bpy, math, os, re
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


def objects_in(x):
    """the objects a modifier, constraint or curve points at (an armature, a hook's empty, a UV warp's bones)."""
    for p in x.bl_rna.properties:
        if p.type == "POINTER" and p.fixed_type and p.fixed_type.identifier == "Object" and getattr(x, p.identifier, None):
            yield getattr(x, p.identifier)


def open_blend(path):
    """A Blender file (its scripts stay off: no -Y): what its first scene renders is put in the empty scene, with all
    that moves it (its skeleton, a hook's empty: kept even where the file hides them), as the file has it, animation
    and constraints too. Its cameras, lights, ground and control shapes stay out."""
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.scenes = src.scenes[:1]
    fs = dst.scenes[0]

    def shown(coll, lc):
        out = [o for o in coll.objects if not o.hide_render]
        for c in coll.children:
            sub = lc.children.get(c.name) if lc else None
            if not c.hide_render and not (sub and sub.exclude):
                out += shown(c, sub)
        return out
    keep = set()

    def need(o):
        if o is None or o in keep:
            return
        keep.add(o)
        need(o.parent)
        for x in list(o.modifiers) + list(o.constraints) + ([o.data] if o.type == "CURVE" else []):
            for t in objects_in(x):
                need(t)
    for o in shown(fs.collection, fs.view_layers[0].layer_collection):
        if o.type in ("MESH", "CURVE", "SURFACE", "META", "FONT"):
            need(o)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end, sc.render.fps = fs.frame_start, fs.frame_end, fs.render.fps
    for o in keep:
        sc.collection.objects.link(o)
    bpy.data.scenes.remove(fs)
    for o in [o for o in bpy.data.objects if o not in keep]:
        bpy.data.objects.remove(o)
    bpy.context.view_layer.update()


def still_blend():
    """a Blender file's guest, still and at rest: each object where its constraints held it (a toy whose pieces hang
    on each other), then no constraints; every skeleton at its rest pose with none (its IK and its controls would
    fight the run). Its skeleton is the one that skins it (a face rig's bones only slide its textures)."""
    bpy.context.view_layer.update()
    at = {o: o.matrix_world.copy() for o in bpy.data.objects}
    for o in bpy.data.objects:
        o.animation_data_clear()
        if o.constraints:
            for c in list(o.constraints):
                o.constraints.remove(c)
            o.matrix_world = at[o]
        if o.type == "ARMATURE":
            o.data.pose_position = "POSE"
            for pb in o.pose.bones:
                for c in list(pb.constraints):
                    pb.constraints.remove(c)
                pb.location, pb.scale = (0, 0, 0), (1, 1, 1)
                pb.rotation_quaternion, pb.rotation_euler = (1, 0, 0, 0), (0, 0, 0)
                pb.rotation_axis_angle = (0, 0, 1, 0)
    bpy.context.view_layer.update()
    skins = lambda a: sum(len(o.data.vertices) for o in bpy.data.objects if o.type == "MESH"
                          for m in o.modifiers if m.type == "ARMATURE" and m.object == a)
    return next(iter(sorted([o for o in bpy.data.objects if o.type == "ARMATURE" and skins(o)], key=skins, reverse=True)), None)


def load(path, action=None, opts=None, keep=None, frame=None, crop=None):
    """Import a guest (glTF, FBX, a Blender file: open_blend) into an empty scene; returns its armature, posable, at
    rest. opts: the importer's options (a rig whose moves come out torn, or that stands in pieces:
    {"guess_original_bind_pose": false}). With action (the name of its own run), it keeps its rest as it came and plays
    that. None: a model with no skeleton (a hover can use it, or make_rig give it one). keep: for a file with several
    characters in it (a crowd of them in every colour), a pattern for the materials of the one to keep; every skeleton
    whose meshes have none goes, with its meshes. frame: a model whose parts the file moves about with no skeleton (a
    toy that transforms): its parts stay as they stand at that frame. crop: [[x, y, z], [x, y, z]], a box (world, as it
    came, after frame): only the parts whose middle is in it stay (a file with two of it side by side). A Blender
    file's guest stands at its rest pose (still_blend)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    blend = path.lower().endswith(".blend")
    if blend:
        open_blend(path)
    elif path.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path, **(opts or {}))
    else:
        bpy.ops.import_scene.gltf(filepath=path, **(opts or {}))
    for o in list(bpy.data.objects):
        if o.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(o)
    for a in [o for o in bpy.data.objects if keep and o.type == "ARMATURE"]:
        parts = [a] + list(a.children_recursive)
        if not any(re.search(keep, m.name) for o in parts if o.type == "MESH" for m in o.data.materials if m):
            for o in parts:
                bpy.data.objects.remove(o)
    if frame is not None:                              # (as they stand at that frame, then still: parents first)
        bpy.context.scene.frame_set(int(frame))
        at = {o: o.matrix_world.copy() for o in bpy.data.objects}
        depth = lambda o: 0 if o.parent is None else 1 + depth(o.parent)
        for o in bpy.data.objects:
            o.animation_data_clear()
        for o in sorted(at, key=depth):
            o.matrix_world = at[o]
        bpy.context.view_layer.update()
    if crop:
        for o in [o for o in bpy.data.objects if o.type == "MESH"]:
            c = sum((o.matrix_world @ Vector(v) for v in o.bound_box), Vector()) / 8
            if not all(crop[0][i] <= c[i] <= crop[1][i] for i in range(3)):
                bpy.data.objects.remove(o)
    for o in bpy.data.objects:
        o.animation_data_clear()
    arm = still_blend() if blend else next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if arm is None:
        return None
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


SPINE = ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "HeadTop")
LIMBS = (("LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToe", "LeftToeEnd"), ("LeftArm", "LeftForeArm", "LeftHand", "LeftHandEnd"))


def make_rig(joints, skip=(), hold=None, rigid=False):
    """A model that came with no skeleton gets one: joints {canonical joint: [x, y, z]}, where each one sits in the
    model as it came (world; a right joint not given is its left one mirrored across x = 0; "HeadTop", "LeftHandEnd",
    "LeftToeEnd": where those bones end), each bone from its joint to the next one down its chain (SPINE, LIMBS: the
    legs hang from the hips, the arms from the last spine bone), skinned with automatic weights (a vertex they miss
    goes to its nearest bone). skip: parts left unskinned (pinned to a bone with attach), as fix_parts names them.
    hold: {bone: [[x, y, z], [x, y, z]]}: the loose pieces (islands) whose middle is in that box move with that bone
    alone (a sword in its hand). rigid: no automatic weights, every loose piece moves with its nearest bone alone (a
    robot of hard parts). Returns the armature, at rest, and its joints {canonical: bone} for find_joints."""
    P = {k: Vector(v) for k, v in joints.items()}
    for k in list(P):
        r = k.replace("Left", "Right")
        if r != k and r not in P:
            P[r] = Vector((-P[k].x, P[k].y, P[k].z))
    chains = [[j for j in SPINE if j in P]]
    chest = next(j for j in reversed(SPINE[:4]) if j in P)
    for limb in LIMBS:
        for side in (limb, [j.replace("Left", "Right") for j in limb]):
            chains.append([("Hips" if "Leg" in side[0] else chest)] + [j for j in side if j in P])
    par, nxt = {}, {}
    for ch in chains:
        for a, b in zip(ch, ch[1:]):
            par[b] = a
            if a not in nxt or a not in ("Hips", chest):    # (the hips and the chest point up the spine)
                nxt[a] = b
    names = [k for k in P if not k.endswith(("End", "Top"))]
    data = bpy.data.armatures.new("made")
    arm = bpy.data.objects.new("made", data)
    bpy.context.scene.collection.objects.link(arm)
    vl = bpy.context.view_layer
    for o in bpy.data.objects:
        o.select_set(o is arm)
    vl.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = data.edit_bones
    for k in names:
        b = eb.new(k)
        b.head = P[k]
        if k in nxt:
            b.tail = P[nxt[k]]
        else:                                          # (an end with nothing given: on along its parent's way)
            b.tail = P[k] + ((P[k] - P[par[k]]) * 0.5 if k in par else Vector((0, 0, 0.1)))
    for k in names:
        if k in par:
            eb[k].parent = eb[par[k]]
    bpy.ops.object.mode_set(mode="OBJECT")
    skip_it = lambda o: any((o.parent and o.parent.name.startswith(s)) or o.name.startswith(s) for s in skip)
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.visible_get() and not skip_it(o)]
    for o in bpy.data.objects:
        o.select_set(o in meshes or o is arm)
    vl.objects.active = arm
    bpy.ops.object.parent_set(type="ARMATURE_NAME" if rigid else "ARMATURE_AUTO", keep_transform=True)
    segs = [(k, P[k], Vector(data.bones[k].tail_local)) for k in names]

    def nearest(p):
        def dist(a, b):
            ab = b - a
            t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-12)))
            return (a + ab * t - p).length
        return min(segs, key=lambda s: dist(s[1], s[2]))[0]
    for o in meshes:
        mw = o.matrix_world
        for isl in (islands(o) if rigid else ()):     # (hard parts: each piece on the bone nearest its middle)
            n = nearest(sum((mw @ o.data.vertices[i].co for i in isl), Vector()) / len(isl))
            (o.vertex_groups.get(n) or o.vertex_groups.new(name=n)).add(isl, 1.0, "REPLACE")
        groups = {g.index: g.name for g in o.vertex_groups}
        for v in o.data.vertices:                     # (heat weighting can miss bits: a seam, a stray piece)
            if sum(g.weight for g in v.groups if groups.get(g.group) in P) < 1e-3:
                n = nearest(mw @ v.co)
                (o.vertex_groups.get(n) or o.vertex_groups.new(name=n)).add([v.index], 1.0, "REPLACE")
        if hold:
            for isl in islands(o):
                c = sum((mw @ o.data.vertices[i].co for i in isl), Vector()) / len(isl)
                for bone, (lo, hi) in hold.items():
                    if all(lo[i] <= c[i] <= hi[i] for i in range(3)):
                        for g in o.vertex_groups:
                            g.remove(isl)
                        (o.vertex_groups.get(bone) or o.vertex_groups.new(name=bone)).add(isl, 1.0, "REPLACE")
                        break
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    vl.update()
    return arm, {k: k for k in names}


def islands(o):
    """a mesh's loose pieces: lists of vertex indices."""
    links = {}
    for e in o.data.edges:
        a, b = e.vertices
        links.setdefault(a, []).append(b); links.setdefault(b, []).append(a)
    seen, out = set(), []
    for v in range(len(o.data.vertices)):
        if v in seen:
            continue
        stack, isl = [v], []
        seen.add(v)
        while stack:
            x = stack.pop(); isl.append(x)
            for y in links.get(x, ()):
                if y not in seen:
                    seen.add(y); stack.append(y)
        out.append(isl)
    return out


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
    named = lambda o, keys: any((o.parent and o.parent.name.startswith(k)) or o.name.startswith(k) for k in keys)
    part = lambda o, keys: o.type == "MESH" and named(o, keys)
    vl = bpy.context.view_layer
    for o in bpy.data.objects:
        if o.type in ("MESH", "CURVE") and named(o, hide):  # (a curve too: a puppet's rods)
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


def matte():
    """No metal anywhere: a game rip's packed map read as metallic turns its cloth black (there's no world for metal
    to reflect, only the hounds' lights)."""
    for m in bpy.data.materials:
        for n in (m.node_tree.nodes if m.node_tree else ()):
            if n.type == "BSDF_PRINCIPLED":
                for l in list(n.inputs["Metallic"].links):
                    m.node_tree.links.remove(l)
                n.inputs["Metallic"].default_value = 0.0


def dither():
    """Every material that blends its alpha in draws dithered instead: EEVEE doesn't sort a blended mesh's faces, so a
    game rip's body showed its own far side through it, in dark blotches."""
    for m in bpy.data.materials:
        if m.surface_render_method == "BLENDED":
            m.surface_render_method = "DITHERED"


def recolor(folder, paint=None, hue=None):
    """paint {material: image file in the model's folder}: the image, by its UVs, as that material's colour (an old
    file's texture slot, which Blender no longer reads). hue {material name start: degrees}: every colour those
    materials set turned that far round the colour wheel (a felt frog that came out mustard)."""
    import colorsys
    for name, f in (paint or {}).items():
        nt = bpy.data.materials[name].node_tree
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
        shader = out.inputs["Surface"].links[0].from_node
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(os.path.join(folder, f))
        nt.links.new(tex.outputs["Color"], shader.inputs["Base Color" if "Base Color" in shader.inputs else "Color"])
    for m in bpy.data.materials:
        deg = next((d for k, d in (hue or {}).items() if m.name.startswith(k)), None)
        if deg is None:
            continue
        turn_it = lambda c: (*colorsys.hsv_to_rgb((colorsys.rgb_to_hsv(*c[:3])[0] + deg / 360) % 1, *colorsys.rgb_to_hsv(*c[:3])[1:]), c[3])
        m.diffuse_color = turn_it(m.diffuse_color)
        for n in (m.node_tree.nodes if m.node_tree else ()):
            for i in n.inputs:
                if i.type == "RGBA" and not i.is_linked:
                    i.default_value = turn_it(i.default_value)


def find_joints(arm, own=None):
    """canonical -> bone name: the family whose names this skeleton has the most of, then the guest's own. A left
    joint's right one is its name mirrored (SIDES); a name SIDES can't mirror ("L_Knee", "leg_lf") names its right
    joint itself ("RightLeg": ...)."""
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
        for kk, vv in ((k, v),) + (((k.replace("Left", "Right"), right(v)),) if "Left" not in k or right(v) != v else ()):
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
    own run, as it came: its rest needn't stand it up). turn: degrees to turn its forward about up (a clip that runs
    another way than the rest faces: we'd see its back)."""

    def __init__(self, arm, joints, axes=None, level=False, turn=0):
        self.arm, self.J = arm, joints
        mw = arm.matrix_world
        self.A = mw.to_3x3().normalized().to_quaternion()
        self.k = mw.to_scale()[0]
        self.bones = sorted(arm.data.bones, key=lambda b: len(b.parent_recursive))
        self.rest = {b.name: b.matrix_local.to_quaternion() for b in self.bones}
        self.head = {b.name: mw @ b.head_local for b in self.bones}
        self.dir = {b.name: (self.A @ (b.tail_local - b.head_local)).normalized() for b in self.bones}
        self.tdir, self.ends = {}, set()               # a loose part's way: to its next bone (not a swing bone's _Handle),
        for b in self.bones:                           # the last one on in its parent's line
            if b.children and all(base(c.name).endswith("_end") for c in b.children):
                self.ends.add(b.name)                  # (an exporter's end marker, a guess: find_loose checks it)
            d = next((self.head[c.name] - self.head[b.name] for c in b.children
                      if "Handle" not in c.name and (self.head[c.name] - self.head[b.name]).length > 1e-6), None)
            self.tdir[b.name] = d.normalized() if d is not None else self.tdir[b.parent.name] if b.parent else self.dir[b.name]
        self.loose, self.bends = {}, {}
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
        if turn:
            q = Quaternion(self.U, math.radians(turn))
            self.L, self.F = q @ self.L, q @ self.F
        self.ground = min(self.at(s + "Foot").dot(self.U) for s in ("Left", "Right"))
        self.leg = (self.at("LeftUpLeg") - self.at("LeftLeg")).length + (self.at("LeftLeg") - self.at("LeftFoot")).length

    def at(self, j):
        return self.head[self.J[j]]

    def v(self, l, f, u, s=1):
        return (self.L * (l * s) + self.F * f + self.U * u).normalized()

    def apply(self, spec, hips=Vector()):
        """spec: canonical joint -> ("aim", dir) | ("rel", Quaternion) | ("ik", foot target) | ("trail", toward, share,
        wave degrees: a loose part turns share of the way toward where the wind blows it, then waves about l);
        hips: a world offset. The bent parts (bends) hold their turn whatever the pose."""
        J, D, pos = self.J, {}, {}
        sp = {**{n: ("rel", q) for n, q in self.bends.items()}, **{J.get(k, k): v for k, v in spec.items()}}  # (canonical joints, or bone names)
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
                pole = (self.F - w * self.F.dot(w)).normalized() * getattr(self, "knee", 1)   # (-1: a walker's knees bend back)
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
       "arm": 0.6, "elbow": 95, "nod": -8, "hip": 0, "arm_lean": 0, "knee": 1, "out": 0.12, "still": ()}

# the loose parts that stream behind a runner (a game rip's swing bones: Sp_<where>_<what>): bone-name pattern ->
# [share (how far each bone turns toward the wind), lift (degrees above level behind it: the wind's way), wave (degrees)]
TRAIL = {r"^Sp_Hi_Tail": [0.45, 5, 9], r"^Sp_He_Hair[1-9]": [0.5, -20, 5],
         r"^Sp_(Ch|He|Hi)_(Jacket|Ribbon|Acc|Collar)": [0.2, -55, 4]}


def find_loose(rig, trail=None):
    """the bones of the loose parts (TRAIL, or the guest's own "trail"): {bone: [share, lift, wave, k]}, k its place
    down the chain (the wave runs down it). Never a joint, nor a swing bone's _Handle end. The last bone of a chain
    that ends in an exporter's _end marker pointing away from its own mesh (the marker's place is a guess: a tail's
    tip went forward, and the wind bent it up in a hook) points at the middle of that mesh instead."""
    out, joints = {}, set(rig.J.values())
    for pat, (share, lift, wave) in (TRAIL if trail is None else trail).items():
        for b in rig.bones:
            if b.name in joints or "Handle" in b.name or not re.search(pat, base(b.name)):
                continue
            k = sum(1 for p in b.parent_recursive if re.search(pat, base(p.name)))
            out[b.name] = [share, lift, wave, k]
            ps = own_mesh(b.name) if b.name in rig.ends else []
            if ps:
                d = sum(ps, Vector()) / len(ps) - rig.head[b.name]
                if d.length > 1e-6 and d.dot(rig.tdir[b.name]) < 0:
                    rig.tdir[b.name] = d.normalized()
    return out


def own_mesh(bone):
    """the points of the meshes that bone moves (weighted half or more to it), where they stand."""
    out = []
    for o in bpy.data.objects:
        g = o.vertex_groups.get(bone) if o.type == "MESH" and o.visible_get() else None
        if g:
            mw = o.matrix_world
            out += [mw @ v.co for v in o.data.vertices if any(x.group == g.index and x.weight >= 0.5 for x in v.groups)]
    return out


def find_bends(rig, bend=None):
    """the parts held at a turn from how they rest (the guest's "bend": a dragon's wings, folded back): {bone-name
    pattern: [degrees about up (+ swings a left part back, a right one forward), degrees about its left (- tips the
    top back)]} -> {bone: Quaternion}; what hangs from them follows."""
    out = {}
    for pat, (yaw, tilt) in (bend or {}).items():
        q = Quaternion(rig.L, math.radians(tilt)) @ Quaternion(rig.U, math.radians(yaw))
        for b in rig.bones:
            if re.search(pat, base(b.name)):
                out[b.name] = q
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
    bent), arm_lean (the share of the lean the arms follow), out (how far out to the side the arms swing: a big belly
    needs them wide), nod (the head, against the lean), knee (-1: the knees bend backward, a walker's or a bird's),
    still (joints left as they stand, following their parent: an arm holding a sword). The loose parts (rig.loose)
    stream behind; a rig with no arms (an AT-ST) swings none. Returns {side: on the ground} for the feet."""
    P = {**RUN, **(P or {})}
    U, F, L, leg = rig.U, rig.F, rig.L, rig.leg
    rig.knee = P["knee"]
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
        if side + "ForeArm" not in rig.J:
            continue
        sw = math.cos(2 * math.pi * (p - beta / 2))     # +1: this leg forward
        a = -sw * P["arm"]                               # the arm on this side swings against it
        up = rig.v(P["out"], a * 1.1, -0.8, s)
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
    for j in P["still"]:
        spec.pop(j, None)
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
    lo, hi = span(rig)
    back = max(30, 3 * (hi - lo))                       # (a rip in cm stands ~180 units tall: 30 is inside it, and
    cam.location = centre - rig.L * back                # the near side it cut away showed its insides)
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = Matrix((rig.F, rig.U, -rig.L)).transposed().to_quaternion()   # x right = F, y up = U, looking along +L
    cam.data.clip_end = max(100, 2 * back)


# ---------------------------------------------------------------------------------------------- a centaur
GALLOP = {"stance": 0.36, "swing": 30, "carpus": 100, "lift": 35, "stifle": 45, "hock": 70, "fetlock": 30,
          "pitch": 5, "bounce": 0.05, "lean": -4, "torso": 0, "arm": 0,
          "lead": {"rear_l": 0.0, "rear_r": 0.09, "front_r": 0.40, "front_l": 0.49}}   # a rotary gallop's footfalls


def centaur(arm, legs, hips, head, torso=None, arms=(), neck=()):
    """a centaur's rig: legs {"front_l": [bones from the shoulder down to the hoof], "front_r", "rear_l", "rear_r"}
    (base names), its body's root bone, its head, (optional) the root of its rider half and its arms (a bone each:
    they swing from there), its neck (bones: a horse's). Up is the world's, forward from the rear legs to the front."""
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
    rig.neck = [names[n] for n in neck or () if n in names]
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


# ---------------------------------------------------------------------------------------------- a horse
# A transverse gallop from key poses (after Muybridge): each leg's four segments (the upper arm or thigh, the forearm or
# shin, the cannon, the pastern with its hoof) at an angle from straight down (degrees, +: its end forward of its
# start), at a share of that leg's stride from its hoof landing; the hoof is down until stance. The far hind lands
# first, then the near hind, the far fore and the near (leading) fore; then all four fold under it in the air.
HORSE = {"lead": {"rear_l": 0.0, "rear_r": 0.12, "front_l": 0.30, "front_r": 0.42}, "stance": {"front": 0.30, "rear": 0.28},
         "front": [[0.00, -8, 22, 24, 58], [0.08, -18, 8, 8, 60], [0.15, -28, -4, -4, 58], [0.23, -38, -18, -20, 35],
                   [0.30, -46, -30, -33, -30], [0.38, -42, -25, -85, -125], [0.46, -30, 5, -105, -140],
                   [0.54, -18, 45, -80, -120], [0.62, -8, 78, -30, -60], [0.70, -2, 75, 30, 10],
                   [0.78, -2, 52, 45, 55], [0.88, -4, 34, 34, 60]],
         "rear": [[0.00, 42, -16, 14, 45], [0.07, 32, -28, 2, 52], [0.14, 20, -42, -12, 55], [0.21, 6, -50, -28, 35],
                  [0.28, -8, -52, -45, -35], [0.36, -12, -60, -62, -110], [0.46, -2, -72, -35, -95],
                  [0.56, 15, -78, 15, -50], [0.66, 32, -70, 50, -5], [0.76, 45, -52, 50, 25], [0.88, 48, -32, 30, 45]],
         "pitch": 3, "hop": 0.02, "neck": 22, "nod": 5, "head": 30}


def horse(rig):
    """a horse's legs as its side view sees them, for horse_gait (rig: centaur's, each leg four bones from the
    shoulder or hip down to the pastern): each leg's root (f, u from the hips), its segments' lengths and angles at
    rest (head to next head; the pastern's to the middle of its hoof) and two points of its hoof, the lowest and the
    toe (whichever is lower stands on the ground), as (length, degrees off the pastern's way). Its ground: the lowest
    point of its meshes (the leg bones' ends are an exporter's guess)."""
    H0 = rig.head[rig.J["Hips"]]
    fu = lambda p: ((p - H0).dot(rig.F), (p - H0).dot(rig.U))
    ang = lambda d: math.degrees(math.atan2(d[0], -d[1]))
    rig.model = {}
    for leg, bones in rig.legs.items():
        pts = [fu(rig.head[b]) for b in bones[:4]]
        seg = [(p[0] - q[0], p[1] - q[1]) for q, p in zip(pts, pts[1:])]
        (f3, u3), hoof = pts[3], [fu(p) for p in own_mesh(bones[3])]
        mid = (sum(p[0] for p in hoof) / len(hoof) - f3, sum(p[1] for p in hoof) / len(hoof) - u3)
        low = min(hoof, key=lambda p: p[1])
        toe = max(hoof, key=lambda p: math.hypot(p[0] - f3, p[1] - u3))
        tips = [(math.hypot(p[0] - f3, p[1] - u3), ang((p[0] - f3, p[1] - u3)) - ang(mid)) for p in (low, toe)]
        rig.model[leg] = {"root": pts[0], "l": (rig.head[bones[0]] - H0).dot(rig.L), "len": [math.hypot(*d) for d in seg],
                          "rest": [ang(d) for d in seg] + [ang(mid)], "tips": tips}
    rig.ground = span(rig)[0]


def _keys(keys, p):
    """the angles at p (0..1) on a loop through key poses [[p, angle, ...], ...] (a Catmull-Rom spline)."""
    n = len(keys)
    i = max((j for j in range(n) if keys[j][0] <= p), default=n - 1)
    a, b, c, d = (keys[(i + k) % n] for k in (-1, 0, 1, 2))
    t = ((p - b[0]) % 1) / ((c[0] - b[0]) % 1 or 1)
    return [0.5 * (2 * y1 + (y2 - y0) * t + (2 * y0 - 5 * y1 + 4 * y2 - y3) * t * t + (3 * y1 - y0 - 3 * y2 + y3) * t ** 3)
            for y0, y1, y2, y3 in zip(a[1:], b[1:], c[1:], d[1:])]


def _seg(length, deg):
    return length * math.sin(math.radians(deg)), -length * math.cos(math.radians(deg))


def _sole(m, a):
    """from a leg's fetlock to where its hoof stands (the lower of its two points), the pastern at a[3]."""
    return min((_seg(length, a[3] + off) for length, off in m["tips"]), key=lambda q: q[1])


def _hoof(m, root, a):
    """where a leg's hoof stands (f, u), the leg at angles a from root."""
    f, u = root
    for length, deg in zip(m["len"], a):
        d = _seg(length, deg); f += d[0]; u += d[1]
    d = _sole(m, a)
    return f + d[0], u + d[1]


def _reach(m, root, a, T, bend):
    """a with its top two angles turned to put the hoof on T (two-bone IK; bend 1: the joint between them forward,
    a stifle; -1: back, an elbow)."""
    l1, l2 = m["len"][:2]
    c, h = _seg(m["len"][2], a[2]), _sole(m, a)
    J = (T[0] - c[0] - h[0] - root[0], T[1] - c[1] - h[1] - root[1])
    dj = math.hypot(*J); d = min(dj, (l1 + l2) * 0.999)
    x = (l1 * l1 - l2 * l2 + d * d) / (2 * d); y = math.sqrt(max(0.0, l1 * l1 - x * x))
    w = (J[0] / dj, J[1] / dj)
    k = (w[0] * x - w[1] * y * bend, w[1] * x + w[0] * y * bend)
    return [math.degrees(math.atan2(k[0], -k[1])), math.degrees(math.atan2(J[0] - k[0], k[1] - J[1]))] + a[2:]


def _root(m, pitch, up):
    """a leg's root (f, u) with the body pitched (degrees, nose up) about the hips and raised up."""
    (f, u), c, s = m["root"], math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    return f * c - u * s, f * s + u * c + up


def horse_gait(rig, n, P=None):
    """a stride of n frames of the gallop (HORSE, the guest's "gallop"): the body pitches (nose up as the hinds land)
    and rides as high as its planted hooves, as keyed, hold it up (in the air: a hop above the heights either side);
    then each planted hoof's top two joints keep it on the ground. Returns [{"up": the hips (m, from rest), "pitch":
    degrees, "legs": {leg: (angles, down, hoof (f, u))}}] and sets rig.travel (how far a stride carries it: as far as
    a planted hoof slides back, over its share of the stride)."""
    P = {**HORSE, **(P or {})}
    g = rig.ground - rig.head[rig.J["Hips"]].dot(rig.U)
    kind = lambda leg: "front" if leg.startswith("front") else "rear"
    phase = lambda leg, i: (i / n - P["lead"][leg]) % 1
    pitch = [P["pitch"] * math.cos(2 * math.pi * (i / n - 0.95)) for i in range(n)]
    need = []
    for i in range(n):
        req = [g - _hoof(m, _root(m, pitch[i], 0), _keys(P[kind(leg)], phase(leg, i)))[1]
               for leg, m in rig.model.items() if phase(leg, i) < P["stance"][kind(leg)]]
        need.append(sum(req) / len(req) if req else None)
    up = list(need)
    for i in range(n):                                 # (in the air: from the height before to the one after, a hop up)
        if need[i] is None:
            j = next(k for k in range(1, n) if need[(i - k) % n] is not None)
            l = next(k for k in range(1, n) if need[(i + k) % n] is not None)
            t = j / (j + l)
            up[i] = need[(i - j) % n] * (1 - t) + need[(i + l) % n] * t + P["hop"] * rig.leg * math.sin(math.pi * t)
    up = [(up[i - 1] + 2 * up[i] + up[(i + 1) % n]) / 4 for i in range(n)]
    out = []
    for i in range(n):
        fr = {"up": up[i], "pitch": pitch[i], "legs": {}}
        for leg, m in rig.model.items():
            k, p = kind(leg), phase(leg, i)
            a, root, down = _keys(P[k], p), _root(m, pitch[i], up[i]), p < P["stance"][k]
            if down:
                a = _reach(m, root, a, (_hoof(m, root, a)[0], g), 1 if k == "rear" else -1)
            fr["legs"][leg] = (a, down, _hoof(m, root, a))
        out.append(fr)
    sweep = [(_hoof(m, m["root"], _keys(P[kind(leg)], 0))[0] - _hoof(m, m["root"], _keys(P[kind(leg)], P["stance"][kind(leg)]))[0])
             / P["stance"][kind(leg)] for leg, m in rig.model.items()]
    rig.travel = sum(sweep) / len(sweep)
    return out


def horse_pose(rig, fr, ph, P=None):
    """pose one frame of horse_gait (fr, at phase ph): the legs, the body pitched and raised, the neck stretched out
    low (neck: degrees below how it stands, nodding nod degrees, lowest on the forelegs), the head's nose out (head
    degrees), the loose parts streaming behind. Returns ({leg: hoof down}, {leg: where its hoof stands, world})."""
    P = {**HORSE, **(P or {})}
    rot = lambda deg: Quaternion(rig.L, math.radians(deg))   # + turns a part's end back (clockwise, seen from its right)
    spec, down, hooves, H0 = {"Hips": ("rel", rot(-fr["pitch"]))}, {}, {}, rig.head[rig.J["Hips"]]
    for leg, (a, dn, h) in fr["legs"].items():
        m, prev = rig.model[leg], -fr["pitch"]
        for bone, rest, deg in zip(rig.legs[leg], m["rest"], a):
            spec[bone] = ("rel", rot(rest - deg - prev)); prev = rest - deg
        down[leg] = dn
        hooves[leg] = H0 + rig.F * h[0] + rig.U * h[1] + rig.L * m["l"]
    nod = P["nod"] * math.cos(2 * math.pi * (ph - 0.5))
    for bone in rig.neck:
        spec[bone] = ("rel", rot((P["neck"] + nod) / len(rig.neck)))
    spec["Head"] = ("rel", rot(-P["head"] - P["neck"] - nod / 2 if rig.neck else -P["head"]))
    trail(rig, spec, ph)
    rig.apply(spec, rig.U * fr["up"])
    return down, hooves


# ---------------------------------------------------------------------------------------------- a hover
HOVER = {"bob": 0.05, "pitch": 3, "waves": 1, "lift": 0.15}
AXES = {"+x": Vector((1, 0, 0)), "-x": Vector((-1, 0, 0)), "+y": Vector((0, 1, 0)), "-y": Vector((0, -1, 0))}


class Body:
    """A guest that floats, rolls or drives along instead of running (a podracer, a train, a car): no feet, maybe no
    skeleton at all, so its frame comes from the spec: forward (the way it faces as it came, AXES), up the world's.
    Its size is its box in that frame: ground (its lowest point), leg (half its height: what the anchors are offset
    by), length, centre. at() / now(): "Hips" its centre, "Head" the top of its front, at rest / as posed."""

    def __init__(self, F, arm=None):
        self.arm, self.U = arm, Vector((0, 0, 1))
        self.F = (F - self.U * F.dot(self.U)).normalized()
        self.L = self.U.cross(self.F).normalized()
        self.J, self.loose = {"Hips": "Hips", "Head": "Head"}, {}
        lo, hi = [math.inf] * 3, [-math.inf] * 3
        dg = bpy.context.evaluated_depsgraph_get()
        for o in bpy.data.objects:
            if o.type == "MESH" and o.visible_get():
                e = o.evaluated_get(dg); m = e.to_mesh(); mw = e.matrix_world
                vs = list(m.vertices)
                for v in vs[::max(1, len(vs) // 20000)]:
                    p = mw @ v.co
                    for i, ax in enumerate((self.L, self.F, self.U)):
                        lo[i], hi[i] = min(lo[i], p.dot(ax)), max(hi[i], p.dot(ax))
                e.to_mesh_clear()
        self.ground, self.length, self.leg = lo[2], hi[1] - lo[1], (hi[2] - lo[2]) / 2
        c = self.L * ((lo[0] + hi[0]) / 2) + self.F * ((lo[1] + hi[1]) / 2) + self.U * ((lo[2] + hi[2]) / 2)
        self.head = {"Hips": c, "Head": c + self.F * (self.length * 0.35) + self.U * (self.leg * 0.7)}
        self.pos = dict(self.head)

    def at(self, j):
        return self.head[j]

    def now(self, j):
        return self.pos[j]


def hover_pose(body, piv, rest, ph, P=None):
    """one frame of a hover at phase ph: the whole model bobs (bob: a share of its height, waves times a stride) and
    pitches (degrees, nose up as it rises) about its centre; piv (the empty it all hangs on, rest: its matrix as it
    stood). Returns the move, a world matrix (what it did to every point that stood still)."""
    P = {**HOVER, **(P or {})}
    w = 2 * math.pi * P["waves"] * ph
    c = body.head["Hips"]
    T = (Matrix.Translation(c + body.U * (P["bob"] * 2 * body.leg * math.sin(w)))
         @ Quaternion(body.L, -math.radians(P["pitch"] * math.sin(w + 1.2))).to_matrix().to_4x4()
         @ Matrix.Translation(-c))
    piv.matrix_world = T @ rest
    bpy.context.view_layer.update()
    body.pos = {k: T @ v for k, v in body.head.items()}
    return T


def front_point(body):
    """the front of a hover as it stands (its nose: the frontmost point of its meshes, at its middle height or above
    if nothing is), for the anchors' mouth."""
    dg, best = bpy.context.evaluated_depsgraph_get(), None
    for o in bpy.data.objects:
        if o.type == "MESH" and o.visible_get():
            e = o.evaluated_get(dg); m = e.to_mesh(); mw = e.matrix_world
            vs = list(m.vertices)
            for v in vs[::max(1, len(vs) // 20000)]:
                p = mw @ v.co
                if best is None or p.dot(body.F) > best.dot(body.F):
                    best = p
            e.to_mesh_clear()
    return best


def wheel_bones(arm, body, meshes=None):
    """a rigged hover's wheels: the bones whose own mesh (weighted half or more to it, as it stands posed) is a disc
    on edge across it (as tall as it is long, within 12%, and thinner side to side) with its bottom near the ground
    (a mudguard that moves with the axle isn't round). [(bone, its middle in the bone's own space, radius)], none
    inside another (it turns with it). meshes: only these (all that show)."""
    dg, pts = bpy.context.evaluated_depsgraph_get(), {}
    for o in (meshes if meshes is not None else bpy.data.objects):
        if o.type == "MESH" and o.visible_get() and o.vertex_groups:
            e = o.evaluated_get(dg); m = e.to_mesh(); mw = e.matrix_world
            if len(m.vertices) == len(o.data.vertices):              # (its skin applied, vertex for vertex)
                names = {g.index: g.name for g in o.vertex_groups}
                for v, ev in zip(o.data.vertices, m.vertices):
                    for g in v.groups:
                        if g.weight >= 0.5 and names[g.group] in arm.pose.bones:
                            pts.setdefault(names[g.group], []).append(mw @ ev.co)
            e.to_mesh_clear()
    found = {}
    for name, ps in pts.items():
        if len(ps) < 12:
            continue
        lo = [min(p.dot(a) for p in ps) for a in (body.L, body.F, body.U)]
        hi = [max(p.dot(a) for p in ps) for a in (body.L, body.F, body.U)]
        d = [b - a for a, b in zip(lo, hi)]
        r = (d[1] + d[2]) / 4
        if abs(d[1] - d[2]) < 0.12 * max(d[1], d[2]) and d[0] < 0.8 * max(d[1], d[2]) and lo[2] - body.ground < 0.25 * r:
            mid = sum((a * ((x + y) / 2) for a, x, y in zip((body.L, body.F, body.U), lo, hi)), Vector())
            found[name] = ((arm.matrix_world @ arm.pose.bones[name].matrix).inverted() @ mid, r)
    inside = lambda b: any(p.name in found for p in b.parent_recursive)
    return [(n, c, r) for n, (c, r) in found.items() if not inside(arm.pose.bones[n].bone)]


def keyed(arm, bones):
    """the curves of arm's clip that key those bones."""
    ad, out = arm.animation_data, []
    for layer in (ad.action.layers if ad and ad.action else ()):
        for s in layer.strips:
            cb = s.channelbag(ad.action_slot)
            out += [fc for fc in (cb.fcurves if cb else ()) if any(fc.data_path.startswith(f'pose.bones["{b}"]') for b in bones)]
    return out


def spin_wheels(arm, wheels, axle, deg, curves=()):
    """turn the wheels (wheel_bones) deg about the axle (armature space: positive rolls it forward) through their
    middles, from wherever the pose has them. curves: the clip's on them (keyed), muted from here on, or the render
    plays the clip over the turn; unmute them before the next frame_set."""
    for fc in curves:
        fc.mute = True
    for name, mid, r in wheels:
        pb = arm.pose.bones[name]
        c = pb.matrix @ mid
        pb.matrix = Matrix.Translation(c) @ Matrix.Rotation(math.radians(deg), 4, axle) @ Matrix.Translation(-c) @ pb.matrix
    bpy.context.view_layer.update()
