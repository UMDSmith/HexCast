"""A guest drawn in 2D: a run cycle someone painted (a PNG of frames in a grid, on a plain background) made into what
render_guest.py writes for a 3D one, so export_assets.py packs it the same way.

    python -I art/derby/scripts/import_sprite.py <spec.json> <abs path>/art/derby/source/guests <abs path>/art/derby/out/guests [ids]

A guest with "sprite": {"file": its PNG in the model folder, "cols", "rows": the grid (found from the gaps between
the frames when left out)}, plus the usual height (px it stands, crystals and ears included), w / h (the frame), x
(where its body sits across the frame, 0.5) and stride (px one cycle carries it: a painted cycle has no feet to
measure it by). The frames are read left to right, top to bottom, facing right; "order" (in the sprite) runs them in
another order, their indices in that reading order: frames painted out of order twitch from pose to pose (take the
loop whose silhouettes change least from frame to frame, run the way round that lands after the leap).

The background (the colour around each frame's edge) goes, and any pocket of it the body closes in; the edge keeps a
soft matte with the background's grey taken back out of it. Painted frames never sit quite where the last one did,
so each is moved to put its body (the top of it: legs swing) on one spot and its row's lowest paw on the ground
line. The anchors are measured from what's left: the nose (its frontmost point up high), the mouth and head just
behind it, the tail (its backmost point up high), the paws (the lowest points of the bottom band, front and back of the
body, down when within a few px of the ground). Writes out/guests/<id>/run_NN.png, anchors.json and sheet.png.
Needs Pillow, numpy and scipy (no Blender).
"""
import json, os, sys
import numpy as np
from PIL import Image
from scipy import ndimage

FOOT = 0.06                     # the feet stand this share of the frame above its bottom (as render_guest.py)
NEAR = 14                       # off the background by less than this (0-255, any channel): background
SOFT = (6, 40)                  # the edge's matte: from transparent at the first to solid at the second
DOWN = 4                        # a paw this close to the ground (px) is down

argv = sys.argv[1:]
SPEC, SRC, OUT = argv[0], argv[1], argv[2]
want = set(argv[3].split(",")) if len(argv) > 3 else None


def runs(mask):
    """the spans [start, end) where mask is true."""
    out, s = [], None
    for i, v in enumerate(mask):
        if v and s is None:
            s = i
        elif not v and s is not None:
            out.append((s, i)); s = None
    return out + ([(s, len(mask))] if s is not None else [])


def cells(rgb, cols, rows):
    """the frames' boxes (x0, y0, x1, y1), reading order: from the gaps of background between them, or an even grid."""
    bg = np.median(np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]), axis=0)
    ink = np.abs(rgb - bg).max(axis=2) > 25
    ys, xs = runs(ink.any(axis=1)), runs(ink.any(axis=0))
    H, W = ink.shape
    if not (cols and rows) or (len(xs), len(ys)) == (cols, rows):
        cols, rows = len(xs), len(ys)
        # (the gaps' middles: each frame keeps all the background around it)
        xb = [0] + [(a[1] + b[0]) // 2 for a, b in zip(xs, xs[1:])] + [W]
        yb = [0] + [(a[1] + b[0]) // 2 for a, b in zip(ys, ys[1:])] + [H]
    else:
        xb = [W * i // cols for i in range(cols + 1)]
        yb = [H * i // rows for i in range(rows + 1)]
    return [(xb[c], yb[r], xb[c + 1], yb[r + 1]) for r in range(rows) for c in range(cols)], cols


def cut(rgb):
    """RGBA (float 0-1) of one frame with its background gone."""
    edge = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(edge, axis=0)
    dev = np.abs(rgb - bg).max(axis=2)
    near = dev < NEAR
    lab, n = ndimage.label(near)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    sizes = ndimage.sum(np.ones_like(dev), lab, range(n + 1))
    back = np.isin(lab, list(border)) | ((sizes > 150)[lab] & near)      # (and the pockets it closes in)
    body = ~back
    rim = ndimage.binary_dilation(body, iterations=2) & back
    a = np.where(body, 1.0, 0.0)
    a[rim] = np.clip((dev[rim] - SOFT[0]) / (SOFT[1] - SOFT[0]), 0, 1)
    col = rgb.astype(np.float64)
    soft = (a > 0) & (a < 1)
    col[soft] = bg + (col[soft] - bg) / a[soft][:, None]                 # (the background's grey taken back out)
    out = np.dstack([np.clip(col, 0, 255) / 255.0, a])
    return out


def anchors_of(a, ground, cx):
    """one frame's anchors from its alpha (frame px)."""
    solid = a > 0.5
    ys, xs = np.nonzero(solid)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    W, H = x1 - x0, y1 - y0
    up = solid[:int(y0 + 0.55 * H)]                                     # (the nose: frontmost up high)
    uy, ux = np.nonzero(up)
    i = ux.argmax(); nose = [float(ux[i]), float(uy[i])]
    ty, tx = np.nonzero(solid[:int(y0 + 0.6 * H)])                       # (the tail: backmost up high; a hind leg reaches further)
    j = tx.argmin(); tail = [float(tx[j]), float(ty[j])]
    top = solid[:, int(cx)]
    back = [float(cx), float(np.nonzero(top)[0].min()) if top.any() else float(y0)]
    band = solid[int(y1 - 0.2 * H):]                                     # (the paws: lowest points of the bottom band)
    paws = []
    for s, e in runs(band.any(axis=0)):
        if e - s < 3:
            continue
        col = band[:, s:e]
        low = np.nonzero(col.any(axis=1))[0].max()
        xs_low = np.nonzero(col[low])[0]
        paws.append([float(s + xs_low.mean()), float(int(y1 - 0.2 * H) + low)])
    front = sorted((p for p in paws if p[0] >= cx), key=lambda p: -p[0]) or [max(paws, key=lambda p: p[0])]
    hind = sorted((p for p in paws if p[0] < cx), key=lambda p: p[0]) or [min(paws, key=lambda p: p[0])]
    paw = lambda p: [round(p[0], 1), round(p[1], 1), int(p[1] >= ground - DOWN)]
    r = lambda p: [round(p[0], 1), round(p[1], 1)]
    fr = {"head": r([nose[0] - 0.2 * W, nose[1] - 0.04 * H]), "eye": r([nose[0] - 0.2 * W, nose[1] - 0.06 * H]),
          "mouth": r([nose[0] - 0.04 * W, nose[1] + 0.04 * H]), "hips": r([x0 + 0.3 * W, y0 + 0.5 * H]),
          "tail_base": r([x0 + 0.2 * W, y0 + 0.45 * H]), "tail": r(tail), "back": r(back),
          "LF": paw(front[0]), "RF": paw(front[1] if len(front) > 1 else front[0]),
          "LH": paw(hind[0]), "RH": paw(hind[1] if len(hind) > 1 else hind[0])}
    return fr, nose


def body_x(a):
    """where its body is across (px): the middle of the top 60% of it (the legs swing; the body doesn't)."""
    ys, xs = np.nonzero(a > 0.5)
    top = ys < ys.min() + 0.6 * (ys.max() - ys.min())
    return float(xs[top].mean())


def sprite(spec, gid):
    sp = spec["sprite"]
    path = os.path.join(SRC, spec.get("model", gid), sp["file"])
    rgb = np.asarray(Image.open(path).convert("RGB")).astype(np.float64)
    boxes, cols = cells(rgb, sp.get("cols"), sp.get("rows"))
    frames = [cut(rgb[y0:y1, x0:x1]) for x0, y0, x1, y1 in boxes]
    # each row's lowest paw is its ground (painted rows rarely agree), the body's top on one spot across
    bottoms = [float(np.nonzero((f[:, :, 3] > 0.5).any(axis=1))[0].max()) for f in frames]
    row_ground = [max(bottoms[i - i % cols:i - i % cols + cols]) for i in range(len(frames))]
    tops = [float(np.nonzero((f[:, :, 3] > 0.5).any(axis=1))[0].min()) for f in frames]
    tall = float(np.median([g - t for g, t in zip(row_ground, tops)]))
    order = sp.get("order") or range(len(frames))                       # (painted out of order: the loop they run in)
    frames, row_ground = [frames[i] for i in order], [row_ground[i] for i in order]
    k = spec.get("height", 120) / tall                                   # (painted px -> frame px)
    w, h = spec.get("w", 270), spec.get("h", 240)
    ground = round(h * (1 - FOOT))
    cx = w * spec.get("x", 0.5)
    od = os.path.join(OUT, gid); os.makedirs(od, exist_ok=True)
    outs, files = [], []
    for i, f in enumerate(frames):
        img = Image.fromarray((f * 255).round().astype(np.uint8), "RGBA").convert("RGBa")    # (premultiplied: no halo)
        img = img.resize((max(1, round(f.shape[1] * k)), max(1, round(f.shape[0] * k))), Image.LANCZOS).convert("RGBA")
        sa = np.asarray(img)[:, :, 3] / 255.0
        dx = cx - body_x(sa)
        dy = ground - row_ground[i] * k
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(img, (int(round(dx)), int(round(dy))))                 # (clipped at the frame's edges)
        fp = os.path.join(od, f"run_{i:02d}.png")
        canvas.save(fp); files.append(fp)
        outs.append(np.asarray(canvas)[:, :, 3] / 255.0)
    fr_list, noses = [], []
    for a in outs:
        fr, nose = anchors_of(a, ground, cx)
        fr_list.append(fr); noses.append(nose)
    an = {"w": w, "h": h, "ground": ground, "stride": spec.get("stride") or round(1.6 * w * 0.6, 1),
          "nose": round(max(n[0] for n in noses), 1),
          "body": [round(sum(f["hips"][0] for f in fr_list) / len(fr_list), 1), round(sum(f["hips"][1] for f in fr_list) / len(fr_list), 1)],
          "frames": fr_list, "family": "sprite", "model": sp["file"]}
    with open(os.path.join(od, "anchors.json"), "w", encoding="utf-8") as fh:
        json.dump(an, fh)
    sheet = Image.new("RGBA", (w * 8, h * ((len(files) + 7) // 8)))
    for i, fp in enumerate(files):
        sheet.alpha_composite(Image.open(fp), ((i % 8) * w, (i // 8) * h))
    sheet.save(os.path.join(od, "sheet.png"))
    print("GUEST", gid, "sprite", sp["file"], f"{len(files)} frames, {k:.3f} px per painted px, stride {an['stride']} px")


for spec in json.load(open(SPEC, encoding="utf-8"))["guests"]:
    if "sprite" in spec and (want is None or spec["id"] in want):
        sprite(spec, spec["id"])
