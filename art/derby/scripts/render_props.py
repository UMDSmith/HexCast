"""Render the crowd's junk: every prop in props.json tumbling end over end, and the way it lies once it lands.

    blender -b -P art/derby/scripts/render_props.py -- <abs path>/art/derby/source/props <abs path>/art/derby/out/props [ids]

Reads source/props/<id>/<model> (.glb .gltf .fbx .obj .stl .blend: the first one found). Each model is laid
down (rot), centred and scaled to a diagonal of 1, dirtied/bloodied as props.json says, and lit like the
hounds. Writes out/props/<id>/tumble_<nn>.png (one turn), rest.png (landed), meta.json and sheet.png (to look at).
"""
import bpy, sys, os, json, math
from mathutils import Euler, Matrix, Quaternion, Vector
sys.path.insert(0, os.path.dirname(__file__))
import houndlib as H

S = 128                                         # a frame (square)
N = 16                                          # tumble frames per turn
AXIS = Vector((1.0, 0.45, 0.6)).normalized()    # end over end in the screen, with a wobble out of it
FIT = 1.06                                      # the frame's width in model units (every model: diagonal 1)
MAX_FACES = 60000                               # scans get decimated to this
EXT = (".glb", ".gltf", ".fbx", ".obj", ".stl", ".blend")
BRIGHTER = {"key": 1.6, "lava": 1.8, "fill": 1.7}   # the hounds' lights, turned up to the crowd's: junk is small and the track dark

HERE = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[sys.argv.index("--") + 1:]
SRC, OUT = argv[0], argv[1]
want = set(argv[2].split(",")) if len(argv) > 2 else None
roster = json.load(open(os.path.join(HERE, "..", "props.json"), encoding="utf-8"))["props"]


def find_model(d):
    if not os.path.isdir(d):
        return None
    files = sorted(os.listdir(d))
    for ext in EXT:
        for f in files:
            if f.lower().endswith(ext):
                return os.path.join(d, f)
    return None


def import_model(path):
    """import into the (empty) scene; returns its meshes (cameras and lights that came with it are dropped)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    else:
        with bpy.data.libraries.load(path) as (src, dst):
            dst.objects = list(src.objects)
        for o in dst.objects:
            if o is not None and o.type == "MESH":
                bpy.context.scene.collection.objects.link(o)
    for o in list(bpy.data.objects):
        if o.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(o)
    return [o for o in bpy.data.objects if o.type == "MESH" and o.visible_get()]


def one_object(meshes):
    """bake every mesh (unparented, unskinned, transforms applied) into one object."""
    vl = bpy.context.view_layer
    for o in meshes:
        for m in list(o.modifiers):
            if m.type == "ARMATURE":
                o.modifiers.remove(m)
        mw = o.matrix_world.copy(); o.parent = None; o.matrix_world = mw
        if o.data.users > 1:
            o.data = o.data.copy()
    for o in bpy.data.objects:
        o.select_set(o in meshes)
    vl.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ob = vl.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o in list(bpy.data.objects):
        if o is not ob:
            bpy.data.objects.remove(o)
    return ob


def relight(ob):
    """scans often come unlit (shadeless: their texture as emission); give them a plain surface so the lights
    shade them like everything else, and so they can be dirtied and bloodied."""
    for mat in {s.material for s in ob.material_slots if s.material}:
        nt = mat.node_tree if mat.use_nodes else None
        if nt is None or any(n.type == "BSDF_PRINCIPLED" for n in nt.nodes):
            continue
        em = next((n for n in nt.nodes if n.type == "EMISSION"), None)
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if em is None or out is None:
            continue
        b = nt.nodes.new("ShaderNodeBsdfPrincipled"); b.inputs["Roughness"].default_value = 0.75
        c = em.inputs["Color"]
        if c.is_linked:
            nt.links.new(c.links[0].from_socket, b.inputs["Base Color"])
        else:
            b.inputs["Base Color"].default_value = c.default_value
        nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
        print("  relit", mat.name)


def drop_lid(ob, above):
    """delete the loose pieces lying wholly above that fraction of its height (a tankard's lid)."""
    import bmesh
    bm = bmesh.new(); bm.from_mesh(ob.data)
    zs = [v.co.z for v in bm.verts]; cut = min(zs) + above * (max(zs) - min(zs))
    seen, gone = set(), []
    for v0 in bm.verts:
        if v0 in seen:
            continue
        part, todo = [], [v0]; seen.add(v0)
        while todo:
            v = todo.pop(); part.append(v)
            for e in v.link_edges:
                w = e.other_vert(v)
                if w not in seen:
                    seen.add(w); todo.append(w)
        if min(v.co.z for v in part) >= cut:
            gone += part
    bmesh.ops.delete(bm, geom=gone, context="VERTS")
    bm.to_mesh(ob.data); bm.free()
    print("  lid: dropped", len(gone), "vertices")


def bounds(ob):
    vs = [v.co for v in ob.data.vertices]
    lo = Vector((min(v.x for v in vs), min(v.y for v in vs), min(v.z for v in vs)))
    hi = Vector((max(v.x for v in vs), max(v.y for v in vs), max(v.z for v in vs)))
    return lo, hi


def sock(sockets, name, kind):
    return next(s for s in sockets if s.name == name and s.type == kind)


def mix(nt, a, b, fac, blend="MIX"):
    """colour a mixed with colour b by fac (a socket or a number)."""
    n = nt.nodes.new("ShaderNodeMix"); n.data_type = "RGBA"; n.blend_type = blend
    f = sock(n.inputs, "Factor", "VALUE")
    if isinstance(fac, (int, float)):
        f.default_value = fac
    else:
        nt.links.new(fac, f)
    for s, v in ((sock(n.inputs, "A", "RGBA"), a), (sock(n.inputs, "B", "RGBA"), b)):
        if isinstance(v, (tuple, list)):
            s.default_value = (v[0], v[1], v[2], 1)
        else:
            nt.links.new(v, s)
    return sock(n.outputs, "Result", "RGBA")


def weather(ob, spec, lo, hi):
    """tint, grime and a bloody end, spliced into every material's base colour."""
    tint, grime, blood = spec.get("tint"), spec.get("grime", 0), spec.get("blood")
    if not (tint or grime or blood):
        return
    for mat in {s.material for s in ob.material_slots if s.material}:
        mat.use_nodes = True; nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        base = bsdf.inputs["Base Color"]
        if base.is_linked:
            col = base.links[0].from_socket
        else:
            rgb = nt.nodes.new("ShaderNodeRGB"); rgb.outputs[0].default_value = base.default_value; col = rgb.outputs[0]
        if tint:
            col = mix(nt, col, tint, 1.0, "MULTIPLY")
        if grime:
            nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 7; nz.inputs["Detail"].default_value = 8
            ramp = nt.nodes.new("ShaderNodeValToRGB"); ramp.color_ramp.elements[0].position = 0.42; ramp.color_ramp.elements[1].position = 0.68
            nt.links.new(nz.outputs["Fac"], ramp.inputs["Fac"])
            k = nt.nodes.new("ShaderNodeMath"); k.operation = "MULTIPLY"; k.inputs[1].default_value = grime
            nt.links.new(ramp.outputs["Color"], k.inputs[0])
            col = mix(nt, col, (0.07, 0.045, 0.02), k.outputs[0])
        if blood:
            ax = blood.get("axis", "-z"); i = "xyz".index(ax[-1]); neg = ax.startswith("-")
            tc = nt.nodes.new("ShaderNodeTexCoord"); sep = nt.nodes.new("ShaderNodeSeparateXYZ")
            nt.links.new(tc.outputs["Object"], sep.inputs[0])
            mr = nt.nodes.new("ShaderNodeMapRange"); mr.clamp = True
            end, other = (lo[i], hi[i]) if neg else (hi[i], lo[i])
            mr.inputs["From Min"].default_value = end + (other - end) * blood.get("to", 0.2)
            mr.inputs["From Max"].default_value = end
            nt.links.new(sep.outputs[i], mr.inputs["Value"])
            col = mix(nt, col, (0.16, 0.0, 0.0), mr.outputs["Result"])
            if "Roughness" in bsdf.inputs and not bsdf.inputs["Roughness"].is_linked:
                bsdf.inputs["Roughness"].default_value = min(bsdf.inputs["Roughness"].default_value, 0.45)
        nt.links.new(col, base)


def add_liquid(ob, spec, lo, hi):
    """a disc of blood inside a mug, joined to it so it tumbles with it."""
    q = spec["liquid"]; c = (lo + hi) / 2
    r = q.get("radius", 0.36) * min(hi.x - lo.x, hi.y - lo.y)
    z = lo.z + q.get("level", 0.8) * (hi.z - lo.z)
    dx, dy = q.get("offset", (0, 0))
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=0.01, vertices=40,
                                        location=(c.x + dx * (hi.x - lo.x), c.y + dy * (hi.y - lo.y), z))
    disc = bpy.context.view_layer.objects.active
    m = bpy.data.materials.new("blood"); m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    b.inputs["Base Color"].default_value = (0.18, 0.0, 0.0, 1); b.inputs["Roughness"].default_value = 0.12
    disc.data.materials.append(m)
    for o in bpy.data.objects:
        o.select_set(o in (ob, disc))
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.join()


def px(cam, p):
    from bpy_extras.object_utils import world_to_camera_view
    v = world_to_camera_view(bpy.context.scene, cam, p)
    return [round(v.x * S, 1), round((1 - v.y) * S, 1)]


for spec in roster:
    pid = spec["id"]
    if want and pid not in want:
        continue
    path = find_model(os.path.join(SRC, pid))
    if not path:
        print("PROP", pid, "no model in", os.path.join(SRC, pid))
        continue
    bpy.ops.wm.read_factory_settings(use_empty=True)
    meshes = import_model(path)
    if not meshes:
        print("PROP", pid, "no meshes in", path)
        continue
    ob = one_object(meshes)
    relight(ob)
    if spec.get("lid"):
        drop_lid(ob, spec["lid"])
    faces = len(ob.data.polygons)
    if faces > MAX_FACES:
        d = ob.modifiers.new("dec", "DECIMATE"); d.ratio = MAX_FACES / faces
        bpy.ops.object.modifier_apply(modifier=d.name)
    if spec.get("liquid"):                      # filled the way up it was modelled, then laid down with it
        add_liquid(ob, spec, *bounds(ob))
    ob.data.transform(Euler([math.radians(a) for a in spec.get("rot", (0, 0, 0))]).to_matrix().to_4x4())
    lo, hi = bounds(ob)
    ob.data.transform(Matrix.Scale(1 / (hi - lo).length, 4) @ Matrix.Translation(-(lo + hi) / 2))
    lo, hi = bounds(ob)
    weather(ob, spec, lo, hi)
    cam = H.setup_render(S, S)
    for o in bpy.data.objects:
        if o.type == "LIGHT" and o.name in BRIGHTER:
            o.data.energy *= BRIGHTER[o.name]
    H.frame_camera(cam, -FIT / 2, FIT / 2, -FIT / 2, FIT / 2, S, S, pad=1.0)
    od = os.path.join(OUT, pid); os.makedirs(od, exist_ok=True)
    sc = bpy.context.scene
    ob.rotation_mode = "QUATERNION"
    files = []
    for i in range(N):
        ob.rotation_quaternion = Quaternion(AXIS, 2 * math.pi * i / N)
        sc.render.filepath = os.path.join(od, f"tumble_{i:02d}.png")
        bpy.ops.render.render(write_still=True); files.append(sc.render.filepath)
    ob.rotation_quaternion = Quaternion((1, 0, 0, 0))
    sc.render.filepath = os.path.join(od, "rest.png")
    bpy.ops.render.render(write_still=True); files.append(sc.render.filepath)
    bottom = px(cam, Vector((0, 0, lo.z)))[1] - S / 2          # how far below the centre it touches the ground
    with open(os.path.join(od, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"w": S, "h": S, "frames": N, "rest_bottom": round(bottom, 1), "model": os.path.basename(path),
                   "faces": faces}, f)
    H.grid(files, 9, S, S, os.path.join(od, "sheet.png"))
    print("PROP", pid, os.path.basename(path), faces, "faces; rests", round(bottom, 1), "px below centre")
