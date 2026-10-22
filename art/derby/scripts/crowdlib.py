"""Hellhound Derby crowd: load a Mixamo character, pose it procedurally, light it, frame it.

The downloads are characters in the T-pose with no animation; every move here (idle, cheer, fist pump,
jeer, clap, the overhand throw) is made by aiming bones, so no animation files are needed. Runs inside
Blender (`blender -b -Y -P render_crowd.py -- ...`); see ../README.md.
"""
import bpy, math
from mathutils import Quaternion, Vector

PRE = "mixamorig:"
# (name, frames, loops): one sheet row each, in this order
ANIMS = [("idle", 8, True), ("cheer", 8, True), ("pump", 8, True), ("jeer", 8, True), ("clap", 8, True), ("throw", 8, False)]
RELEASE = 4                     # the throw's frame where the junk leaves the hand
FINGERS = ("Index", "Middle", "Ring", "Pinky")
CURL = (0, 55, 80, 70)          # degrees per finger joint for a full fist (joints 1..3)


def load(path):
    """Import a character FBX into an empty scene; returns the armature, posable (its T-pose action dropped)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=path)
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    for o in bpy.data.objects:
        o.animation_data_clear()
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return arm


def turn(a, b, deg):
    """the rotation that turns direction a toward direction b by deg degrees."""
    ax = a.cross(b)
    return Quaternion(ax.normalized(), math.radians(deg)) if ax.length > 1e-9 and deg else Quaternion()


class Rig:
    """Poses a Mixamo skeleton. Directions are given in the character's own frame: l (its left), f (forward,
    the way it faces), u (up), so one spec poses every character the same way, whatever its proportions.
    Bones are aimed (upper arms, forearms, legs) or turned relative to their parent (spine, head, fingers)."""

    def __init__(self, arm):
        self.arm = arm
        mw = arm.matrix_world
        self.A = mw.to_3x3().normalized().to_quaternion()       # armature space -> world (rotation)
        self.k = mw.to_scale()[0]
        self.bones = sorted(arm.data.bones, key=lambda b: len(b.parent_recursive))
        self.rest = {b.name: b.matrix_local.to_quaternion() for b in self.bones}
        self.head = {b.name: mw @ b.head_local for b in self.bones}
        self.dir = {b.name: (self.A @ (b.tail_local - b.head_local)).normalized() for b in self.bones}
        self.U = Vector((0, 0, 1))
        foot, toe = self.head.get(PRE + "LeftFoot"), self.head.get(PRE + "LeftToeBase")
        f = (toe - foot) if foot and toe else Vector((0, -1, 0))
        f.z = 0
        self.F = f.normalized()
        self.L = self.U.cross(self.F).normalized()
        if (self.head[PRE + "LeftArm"] - self.head[PRE + "RightArm"]).dot(self.L) < 0:
            self.L = -self.L
        top = self.head.get(PRE + "HeadTop_End", self.head[PRE + "Head"])
        self.height = top.z
        self.size = self.height / 1.8                             # moves are written for a 1.8 m body

    def has(self, n):
        return PRE + n in self.rest

    def v(self, l, f, u, s=1):
        """a direction in the character's frame; s = -1 mirrors it for the right side."""
        return (self.L * (l * s) + self.F * f + self.U * u).normalized()

    def length(self, a, b):
        return (self.head[PRE + b] - self.head[PRE + a]).length

    def apply(self, spec, hips=(0, 0, 0)):
        """spec: bone (no prefix) -> ("aim", world dir) | ("rel", Quaternion); hips: (l, f, u) offset in metres
        (scaled to the body). Legs keep their feet planted (two-bone IK)."""
        D, pos = {}, {}
        off = (self.L * hips[0] + self.F * hips[1] + self.U * hips[2]) * self.size
        for side in ("Left", "Right"):
            if self.has(side + "UpLeg") and self.has(side + "Leg") and self.has(side + "Foot"):
                spec.setdefault(side + "UpLeg", ("ik", side))
                spec.setdefault(side + "Foot", ("aim", self.dir[PRE + side + "Foot"]))
        Ai = self.A.inverted()
        for b in self.bones:
            n = b.name; short = n[len(PRE):] if n.startswith(PRE) else n
            p = b.parent.name if b.parent else None
            Dp = D[p] if p else Quaternion()
            pos[n] = (pos[p] + Dp @ (self.head[n] - self.head[p])) if p else self.head[n] + off
            sp = spec.get(short)
            if sp is None:
                Db = Dp
            elif sp[0] == "rel":
                Db = Dp @ sp[1]
            elif sp[0] == "aim":
                Db = (Dp @ self.dir[n]).rotation_difference(sp[1]) @ Dp
            else:                                              # ("ik", side): this is the thigh
                side = sp[1]
                l1, l2 = self.length(side + "UpLeg", side + "Leg"), self.length(side + "Leg", side + "Foot")
                H, T = pos[n], self.head[PRE + side + "Foot"]
                d = min((T - H).length, (l1 + l2) * 0.9995); w = (T - H).normalized()
                a = (l1 * l1 - l2 * l2 + d * d) / (2 * d); h = math.sqrt(max(0.0, l1 * l1 - a * a))
                pole = (self.F - w * self.F.dot(w)).normalized()
                knee = H + w * a + pole * h
                Db = (Dp @ self.dir[n]).rotation_difference((knee - H).normalized()) @ Dp
                spec[side + "Leg"] = ("aim", (T - knee).normalized())
            D[n] = Db
            Dpa, Dba, R = Ai @ Dp @ self.A, Ai @ Db @ self.A, self.rest[n]
            pb = self.arm.pose.bones[n]
            pb.rotation_quaternion = R.inverted() @ Dpa.inverted() @ Dba @ R
            pb.location = (R.inverted() @ (Ai @ off) / self.k) if not p else Vector()
        bpy.context.view_layer.update()

    # ------------------------------------------------------------- the parts of a pose
    def body(self, spec, lean=0.0, twist=0.0, bend=0.0, nod=0.0, look=0.0, tilt=0.0, jaw=0.0):
        """lean forward, twist toward its left, bend to its left (spread over the spine); the head nods
        down, looks left, tilts left; the jaw opens (if the rig has one)."""
        U, F, L = self.U, self.F, self.L
        q = turn(U, F, lean / 3) @ turn(F, L, twist / 3) @ turn(U, L, bend / 3)
        for n in ("Spine", "Spine1", "Spine2"):
            spec[n] = ("rel", q)
        spec["Neck"] = ("rel", turn(U, F, nod * 0.4))
        spec["Head"] = ("rel", turn(U, F, nod * 0.6) @ turn(F, L, look) @ turn(U, L, tilt))
        for n in ("Jaw", "JawDowm", "JawDown", "LowerJaw"):
            if self.has(n):
                spec[n] = ("rel", turn(F, -U, jaw))

    def limb(self, spec, side, upper, fore, curl=1.0, shrug=0.0):
        """aim one arm: upper and fore are (out, forward, up) for that side; curl 0 open .. 1 fist."""
        s = 1 if side == "Left" else -1
        spec[side + "Arm"] = ("aim", self.v(*upper, s=s))
        spec[side + "ForeArm"] = ("aim", self.v(*fore, s=s))
        if shrug:
            spec[side + "Shoulder"] = ("rel", turn(self.L * s, self.U, shrug))
        for fn in FINGERS:
            for j in (1, 2, 3):
                if self.has(f"{side}Hand{fn}{j}"):
                    spec[f"{side}Hand{fn}{j}"] = ("rel", turn(self.L * s, -self.U, CURL[j] * curl))


def _mix(a, b, k):
    if isinstance(a, tuple):
        return tuple(_mix(x, y, k) for x, y in zip(a, b))
    return a + (b - a) * k


def _keys(keys, x):
    """smoothly interpolate a dict of numbers/tuples between keyframes [(x, {...}), ...]."""
    for (x0, a), (x1, b) in zip(keys, keys[1:]):
        if x0 <= x <= x1:
            k = (x - x0) / (x1 - x0) if x1 > x0 else 0
            k = k * k * (3 - 2 * k)
            return {n: _mix(a[n], b[n], k) for n in a}
    return dict(keys[-1][1])


# the throw, one key per frame: R = the throwing (right) arm, L = the other; (out, forward, up)
THROW = [
    dict(R=((0.3, 0.3, -1), (0.1, 0.8, -0.3), 1.0), L=((0.3, 0.3, -1), (0.1, 0.8, -0.3), 1.0), lean=5, twist=0, nod=4, jaw=0, hip=(0, 0, 0)),
    dict(R=((0.8, -0.05, 0.3), (0.25, -0.35, 1), 1.0), L=((0.4, 0.6, -0.3), (0.0, 1, 0.1), 0.6), lean=-2, twist=-14, nod=-2, jaw=6, hip=(0, -0.03, -0.02)),
    dict(R=((0.7, -0.5, 0.55), (0.05, -0.7, 0.7), 1.0), L=((0.3, 0.9, 0.1), (0.0, 1, 0.3), 0.3), lean=-8, twist=-26, nod=-8, jaw=10, hip=(0, -0.05, -0.04)),
    dict(R=((0.45, 0.2, 0.9), (0.05, 0.25, 1), 1.0), L=((0.35, 0.6, -0.4), (0.1, 0.6, -0.6), 0.6), lean=0, twist=-6, nod=-4, jaw=16, hip=(0, 0, -0.03)),
    dict(R=((0.3, 0.85, 0.5), (0.05, 1, 0.25), 0.1), L=((0.45, -0.2, -0.9), (0.2, 0.2, -1), 0.8), lean=12, twist=16, nod=6, jaw=24, hip=(0, 0.06, -0.05)),
    dict(R=((0.05, 0.9, -0.25), (-0.3, 0.85, -0.45), 0.2), L=((0.45, -0.3, -0.85), (0.2, 0.2, -1), 0.8), lean=18, twist=24, nod=10, jaw=20, hip=(0, 0.07, -0.06)),
    dict(R=((-0.05, 0.6, -0.8), (-0.3, 0.5, -0.8), 0.4), L=((0.35, 0.0, -1), (0.15, 0.4, -1), 0.8), lean=15, twist=20, nod=8, jaw=10, hip=(0, 0.05, -0.04)),
    dict(R=((0.2, 0.35, -1), (0.1, 0.7, -0.6), 0.7), L=((0.3, 0.2, -1), (0.1, 0.6, -0.7), 0.8), lean=8, twist=8, nod=5, jaw=4, hip=(0, 0.02, -0.02)),
]


def pose(rig, anim, i, n, seed=0.0):
    """pose frame i of n of an animation. Loops are functions of the phase u (frame n = frame 0)."""
    u = i / n; w = 2 * math.pi * u; spec = {}; c = math.cos; s = math.sin
    if anim == "idle":
        rig.body(spec, lean=4, twist=3 * s(w), bend=2 * s(w + 1), nod=4, look=12 * s(w + 0.6))
        rig.limb(spec, "Left", (0.25, 0.08 + 0.04 * s(w), -1), (0.12, 0.45, -1), 0.5)
        rig.limb(spec, "Right", (0.25, 0.08 - 0.04 * s(w), -1), (0.12, 0.45, -1), 0.5)
        hip = (0.01 * s(w), 0, -0.008 * (0.5 - 0.5 * c(w)))
    elif anim == "cheer":                                   # both fists up, pumping, bouncing
        e = 0.5 - 0.5 * c(w)
        el = math.radians(30 + 52 * e)
        rig.body(spec, lean=6 - 10 * e, nod=-12 * e, jaw=24 * e)
        for side in ("Left", "Right"):
            rig.limb(spec, side, (0.85 * c(el), 0.2, s(el)), (0.05 + 0.35 * (1 - e), 0.15, 1), 1.0, shrug=14 * e)
        hip = (0, 0, -0.08 * (1 - e))
    elif anim == "pump":                                    # the right fist pumps, the left at the belly
        e = 0.5 - 0.5 * c(w)
        rig.body(spec, lean=5 - 7 * e, twist=9 * e, nod=-8 * e, jaw=20 * e)
        rig.limb(spec, "Right", (0.55, 0.35, -0.2 + 1.35 * e), (0.05, 0.25 - 0.1 * e, 1), 1.0, shrug=10 * e)
        rig.limb(spec, "Left", (0.3, 0.25, -1), (0.0, 0.9, -0.3), 1.0)
        hip = (0, 0, -0.045 * (1 - e))
    elif anim == "jeer":                                    # leaning over the rail, fists shaking, yelling
        eL, eR = 0.5 - 0.5 * c(2 * w), 0.5 - 0.5 * c(2 * w + math.pi)
        rig.body(spec, lean=14 + 3 * eL, nod=8 + 4 * eL, look=7 * s(2 * w), jaw=26)
        rig.limb(spec, "Left", (0.35, 0.85, -0.25 + 0.3 * eL), (-0.05, 0.55, 0.6 + 0.35 * eL), 1.0)
        rig.limb(spec, "Right", (0.35, 0.85, -0.25 + 0.3 * eR), (-0.05, 0.55, 0.6 + 0.35 * eR), 1.0)
        hip = (0, 0, -0.025 * eL)
    elif anim == "clap":                                    # two claps per loop
        e = 0.5 - 0.5 * c(2 * w)
        rig.body(spec, lean=6, nod=2, look=9 * s(w), jaw=8)
        for side in ("Left", "Right"):
            rig.limb(spec, side, (0.35, 0.75, -0.55), (-(0.1 + 0.62 * e), 0.75, 0.35), 0.15)
        hip = (0, 0, -0.015 * e)
    else:                                                   # the throw: keyed, one per frame
        k = THROW[i]
        rig.body(spec, lean=k["lean"], twist=k["twist"], nod=k["nod"], jaw=k["jaw"])
        rig.limb(spec, "Right", k["R"][0], k["R"][1], k["R"][2])
        rig.limb(spec, "Left", k["L"][0], k["L"][1], k["L"][2])
        hip = k["hip"]
    rig.apply(spec, hip)


# ---------------------------------------------------------------- render setup
def setup_render(w, h):
    sc = bpy.context.scene
    wd = bpy.data.worlds.new("w"); sc.world = wd; wd.use_nodes = True
    wd.node_tree.nodes["Background"].inputs[0].default_value = (0.012, 0.006, 0.008, 1)
    sc.render.engine = "BLENDER_EEVEE"; sc.eevee.taa_render_samples = 24
    sc.render.film_transparent = True
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.image_settings.file_format = "PNG"; sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "AgX"; sc.view_settings.look = "AgX - Medium High Contrast"
    cam_d = bpy.data.cameras.new("cam"); cam_d.type = "ORTHO"
    cam = bpy.data.objects.new("cam", cam_d); sc.collection.objects.link(cam); sc.camera = cam
    return cam


def light_rig(F):
    """the stands' light, for a character facing F (toward the camera): a warm key from the camera side, a
    hot rim from behind (the lava falls), a bounce from the track below, a cold fill."""
    sc = bpy.context.scene
    L = Vector((0, 0, 1)).cross(F).normalized(); U = Vector((0, 0, 1))

    def light(name, energy, color, travel, angle=2.0):
        d = bpy.data.lights.new(name, "SUN"); d.energy = energy; d.color = color; d.angle = math.radians(angle)
        o = bpy.data.objects.new(name, d); sc.collection.objects.link(o)
        o.rotation_mode = "QUATERNION"; o.rotation_quaternion = travel.normalized().to_track_quat("-Z", "Y")
    light("key", 4.2, (1.0, 0.9, 0.82), -F * 0.6 - L * 0.4 - U * 0.7)
    light("rim", 6.0, (1.0, 0.32, 0.07), F * 0.75 + L * 0.3 - U * 0.55)
    light("rim2", 3.5, (1.0, 0.22, 0.05), F * 0.7 - L * 0.5 - U * 0.4)
    light("lava", 1.6, (1.0, 0.28, 0.06), -F * 0.45 + U * 0.85)
    light("fill", 0.6, (0.5, 0.45, 1.0), -F * 0.5 + L * 0.7 + U * 0.1)


def frame_camera(cam, F, ppm, w, h, z0):
    """an orthographic camera in front of the character: ppm pixels per metre, the bottom of the frame at z0."""
    cam.data.ortho_scale = max(w, h) / ppm
    zc = z0 + h / ppm / 2
    cam.location = F * 30 + Vector((0, 0, zc))
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (-F).to_track_quat("-Z", "Y")
