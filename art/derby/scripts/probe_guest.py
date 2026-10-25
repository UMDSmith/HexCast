"""Look inside guest models before writing their spec: the skeleton, its clips, its size.

    blender -b -P art/derby/scripts/probe_guest.py -- <abs path>/art/derby/source/guests <abs path>/art/derby/out/probe [folders]

For each model folder (the first .glb/.gltf/.fbx in it): the armature's bones (base names, with their parents), the
joint family guestlib finds and the canonical joints it can't, every clip (name, frames, which bones it keys), the
meshes and how tall it stands. Writes out/probe/<folder>.json and prints a summary. A clip whose name says run /
sprint / dash / gallop / drive is likely the one for "anim". No -Y: downloads can carry scripts.
"""
import bpy, sys, os, json, re
from mathutils import Vector
sys.path.insert(0, os.path.dirname(__file__))
import guestlib as G

argv = sys.argv[sys.argv.index("--") + 1:]
SRC, OUT = argv[0], argv[1]
want = set(argv[2].split(",")) if len(argv) > 2 else None
RUNNY = re.compile(r"run|sprint|dash|gallop|drive|race|jog|move|walk|fly|hover|loop", re.I)
os.makedirs(OUT, exist_ok=True)


def find_model(d):
    for ext in (".glb", ".gltf", ".fbx", ".blend"):
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(ext):
                return os.path.join(d, f)
    return None


for folder in sorted(os.listdir(SRC)):
    d = os.path.join(SRC, folder)
    if not os.path.isdir(d) or (want and folder not in want):
        continue
    path = find_model(d)
    if not path:
        continue
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if path.lower().endswith(".blend"):
        G.open_blend(path)
    elif path.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        bpy.ops.import_scene.gltf(filepath=path)
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    for o in meshes:
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo, hi = Vector(map(min, lo, p)), Vector(map(max, hi, p))
    info = {"file": os.path.basename(path), "mb": round(os.path.getsize(path) / 1e6, 1),
            "size_m": [round(x, 3) for x in hi - lo], "meshes": [o.name for o in meshes][:60], "armatures": []}
    for arm in arms:
        bones = [{"name": G.base(b.name), "parent": G.base(b.parent.name) if b.parent else None} for b in arm.data.bones]
        fam, joints = G.find_joints(arm)
        need = ["Hips", "Spine", "Neck", "Head", "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot",
                "LeftArm", "LeftForeArm", "RightArm", "RightForeArm"]
        info["armatures"].append({"name": arm.name, "bones": bones, "family": fam,
                                  "found": {k: G.base(v) for k, v in joints.items()},
                                  "missing": [j for j in need if j not in joints]})
    info["clips"] = []
    for a in bpy.data.actions:
        curves = [fc for ly in a.layers for st in ly.strips for cb in st.channelbags for fc in cb.fcurves]   # (slotted actions)
        keyed = {re.sub(r'^pose\.bones\["(.+?)"\].*$', r"\1", fc.data_path) for fc in curves if fc.data_path.startswith("pose.bones")}
        info["clips"].append({"name": a.name, "frames": [round(a.frame_range[0], 2), round(a.frame_range[1], 2)],
                              "keys_bones": len(keyed), "runny": bool(RUNNY.search(a.name))})
    with open(os.path.join(OUT, folder + ".json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    a0 = info["armatures"][0] if info["armatures"] else None
    print("PROBE", folder, info["file"], info["mb"], "MB", "size", info["size_m"],
          "| rig:", (a0["family"], len(a0["bones"]), "bones, missing", a0["missing"]) if a0 else "none",
          "| clips:", len(info["clips"]), [c["name"] for c in info["clips"] if c["runny"]][:8])
