"""Render every hound's gallop loop (or the ones named) and a preview page.

    blender -b -Y -P art/derby/scripts/render_gallop.py -- <cerber1.fbx> <out dir> [frames] [hound,ids]
"""
import bpy, sys, os, json
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
sys.path.insert(0, os.path.dirname(__file__))
import houndlib as H
import build_preview

# points the overlay hangs effects on (fire trails, paw prints, smoke...): pose-bone (name, end)
ANCHORS = {
    "tail": ("DEF-spine", "head"),             # the tail chain is reversed: the tip is the head of DEF-spine
    "tail_mid": ("DEF-spine.002", "head"),
    "tail_base": ("DEF-spine.003", "tail"),
    "mouth": ("DEF-chin.001", "head"),
    "eye": ("DEF-lid.T.R.002", "head"),        # the near (camera) side is R
    "head": ("DEF-spine.011", "tail"),
    "back": ("DEF-spine.006", "head"),
    "hips": ("DEF-spine.004", "head"),
    "LH": ("DEF-toe.L", "tail"), "RH": ("DEF-toe.R", "tail"),
    "LF": ("DEF-front_toe.L", "tail"), "RF": ("DEF-front_toe.R", "tail"),
}
PAWS = ("LH", "RH", "LF", "RF")

HERE = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[sys.argv.index("--") + 1:]
SRC, OUT = argv[0], argv[1]
N = int(argv[2]) if len(argv) > 2 else 16
roster = json.load(open(os.path.join(HERE, "..", "hounds.json"), encoding="utf-8"))["hounds"]
if len(argv) > 3:
    want = set(argv[3].split(","))
    roster = [h for h in roster if h["id"] in want]
W, Hh = 360, 240


SKULL = os.path.join(HERE, "..", "source", "edinburgh-dog-skull", "source", "Canine Skull.stl")


def setup(hound, g):
    """a fresh scene with the model (with any extra heads, as a skeleton, re-tinted), lights and camera."""
    heads = g.get("heads")
    rig = H.load(SRC); cam = H.setup_render(W, Hh)
    H.clear_action(rig)
    if heads:
        H.add_heads(rig, heads)
    if hound.get("tint"):
        H.tint(hound["tint"])
    if hound.get("build") == "skeleton":
        import harry
        harry.build(rig, SKULL)
    P = H.Poser(rig)
    if heads:
        H.head_offsets(P, heads)
    jaw, ears, piv = H.face_groups(P)
    H.frame_camera(cam, -3.6, 10.4, -0.8, 7.8, W, Hh, pad=1.0)
    return rig, cam, P, jaw, ears, piv


model, built = None, None
for hound in roster:
    g = H.profile(hound.get("gait"))
    look = json.dumps([g.get("heads"), hound.get("tint"), hound.get("build")])
    if model is None or look != built:               # extra heads, tints and skeletons need their own model
        built = look
        model = setup(hound, g)
    rig, cam, P, jaw, ears, piv = model
    rig.scale = (g["scale"],) * 3
    anchors = dict(ANCHORS)
    for hd in g.get("heads") or []:
        anchors["mouth" + hd["sfx"]] = ("DEF-chin.001" + hd["sfx"], "head")
        anchors["head" + hd["sfx"]] = ("DEF-spine.011" + hd["sfx"], "tail")
    d = os.path.join(OUT, hound["id"]); os.makedirs(d, exist_ok=True)
    files, marks = [], []
    sc = bpy.context.scene
    for i, (a, root, down) in enumerate(H.cycle(P, N, jaw, ears, piv, g)):
        P.apply(a, root, piv)
        pts = {}
        for key, (bone, end) in anchors.items():
            v = world_to_camera_view(sc, cam, rig.matrix_world @ getattr(rig.pose.bones[bone], end))
            pts[key] = [round(v.x * W, 1), round((1 - v.y) * Hh, 1)]
        for p in PAWS:
            pts[p].append(1 if down[p] >= 0 else 0)
        marks.append(pts)
        sc.render.filepath = os.path.join(d, f"gallop_{i:02d}.png")
        bpy.ops.render.render(write_still=True)
        files.append(sc.render.filepath)
    # how far the ground moves in one loop: a planted paw is still on the ground, so it slides back
    # exactly as fast as the track goes by
    slide = [marks[i][p][0] - marks[(i + 1) % N][p][0] for i in range(N) for p in PAWS
             if marks[i][p][2] and marks[(i + 1) % N][p][2]]
    stride = round(sum(slide) / len(slide) * N, 1) if slide else 0
    ground = round((1 - world_to_camera_view(sc, cam, Vector((0, 3, 0))).y) * Hh, 1)
    with open(os.path.join(d, "anchors.json"), "w", encoding="utf-8") as f:
        json.dump({"w": W, "h": Hh, "ground": ground, "stride": stride, "frames": marks}, f)
    H.grid(files, 8, W, Hh, os.path.join(d, "gallop_sheet.png"))
    print("HOUND", hound["id"], "done", "stride px", stride, "contacts", [sum(m[p][2] for m in marks) for p in PAWS])

print("PREVIEW", build_preview.build(OUT, N))
