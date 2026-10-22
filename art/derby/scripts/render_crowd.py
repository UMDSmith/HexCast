"""Render the crowd: every character's loops (idle, cheer, fist pump, jeer, clap) and its throw, small and
front on, as the stands see the track.

    blender -b -Y -P art/derby/scripts/render_crowd.py -- <abs path>/art/derby/source/crowd <abs path>/art/derby/out/crowd [ids]

Reads source/crowd/<id>/<anything>.fbx (Mixamo characters, T-pose, no animation needed). Writes
out/crowd/<id>/<anim>_<frame>.png, anchors.json (the ground row, hips, head top, the throwing hand at the
release frame) and sheet.png (a contact sheet to look at).
"""
import bpy, sys, os, json, math
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector
sys.path.insert(0, os.path.dirname(__file__))
import crowdlib as C
import houndlib as H

W, Hh = 120, 140          # one frame
PPM = 44                  # pixels per metre: a 1.8 m monster is about 80 px tall
Z0 = -0.08                # the frame's bottom edge, in metres (the feet stand at 0)
YAW = 15                  # turned a little toward where the hounds go (the overlay mirrors half the crowd)

argv = sys.argv[sys.argv.index("--") + 1:]
SRC, OUT = argv[0], argv[1]
want = set(argv[2].split(",")) if len(argv) > 2 else None


def px(sc, cam, p):
    v = world_to_camera_view(sc, cam, p)
    return [round(v.x * W, 1), round((1 - v.y) * Hh, 1)]


for cid in sorted(os.listdir(SRC)):
    d = os.path.join(SRC, cid)
    fbx = [f for f in os.listdir(d) if f.lower().endswith(".fbx")] if os.path.isdir(d) else []
    if not fbx or (want and cid not in want):
        continue
    arm = C.load(os.path.join(d, fbx[0]))
    rig = C.Rig(arm)
    sc = bpy.context.scene
    cam = C.setup_render(W, Hh)
    C.light_rig(rig.F)
    C.frame_camera(cam, rig.F, PPM, W, Hh, Z0)
    arm.matrix_world = Matrix.Rotation(math.radians(YAW), 4, "Z") @ arm.matrix_world
    od = os.path.join(OUT, cid); os.makedirs(od, exist_ok=True)
    files, clipped, hand = [], set(), None
    for name, n, loops in C.ANIMS:
        for i in range(n):
            C.pose(rig, name, i, n)
            if name == "throw" and i == C.RELEASE:
                pb = arm.pose.bones.get(C.PRE + "RightHandMiddle1")
                p = arm.matrix_world @ (pb.head if pb else arm.pose.bones[C.PRE + "RightHand"].tail)
                hand = px(sc, cam, p)
            sc.render.filepath = os.path.join(od, f"{name}_{i}.png")
            bpy.ops.render.render(write_still=True)
            files.append(sc.render.filepath)
            im = bpy.data.images.load(sc.render.filepath)
            a = np.array(im.pixels[:], dtype=np.float32).reshape(Hh, W, 4)[..., 3]
            bpy.data.images.remove(im)
            if a[-1].max() > 0.05 or a[:, 0].max() > 0.05 or a[:, -1].max() > 0.05:
                clipped.add(name)
    C.pose(rig, "idle", 0, 8)
    bones = arm.pose.bones
    top = bones.get(C.PRE + "HeadTop_End") or bones[C.PRE + "Head"]
    anchors = {"w": W, "h": Hh, "ground": px(sc, cam, Vector((0, 0, 0)))[1],
               "hips": px(sc, cam, arm.matrix_world @ bones[C.PRE + "Hips"].head)[1],
               "top": px(sc, cam, arm.matrix_world @ top.head)[1], "hand": hand, "release": C.RELEASE,
               "anims": [[name, n, loops] for name, n, loops in C.ANIMS], "height": round(rig.height, 2)}
    with open(os.path.join(od, "anchors.json"), "w", encoding="utf-8") as f:
        json.dump(anchors, f)
    H.grid(files, 8, W, Hh, os.path.join(od, "sheet.png"))
    print("CROWD", cid, "height %.2f m" % rig.height, "anchors", anchors["ground"], anchors["hips"], anchors["top"], hand,
          "CLIPPED " + ",".join(sorted(clipped)) if clipped else "")
