"""Copy the rendered hounds into the add-on: one WebP sprite sheet per hound + the roster file.

    blender -b -Y -P art/derby/scripts/export_assets.py -- <abs path>/art/derby/out/gallop <abs path>/catalog/games_derby/static

Writes, under the add-on's static/ folder:
  hounds/<id>.webp      the gallop loop: frames in a COLS x ROWS grid, left to right, top to bottom
  derby_hounds.json     the roster (art/derby/hounds.json: names, colours, effects) + each hound's sheet
                        and anchor points (art/derby/out/gallop/<id>/anchors.json)
  derby_lines.json      what they say (art/derby/lines.json)
The server reads the roster (ids, names) and the lines; the overlay reads all of it.
"""
import bpy, sys, os, json, glob
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[sys.argv.index("--") + 1:]
SRC, DST = argv[0], argv[1]
COLS = 8
QUALITY = 82

roster = json.load(open(os.path.join(HERE, "..", "hounds.json"), encoding="utf-8"))["hounds"]
lines = json.load(open(os.path.join(HERE, "..", "lines.json"), encoding="utf-8"))
os.makedirs(os.path.join(DST, "hounds"), exist_ok=True)
sc = bpy.context.scene
s = sc.render.image_settings
s.file_format = "WEBP"; s.color_mode = "RGBA"; s.quality = QUALITY
sc.view_settings.view_transform = "Standard"      # the frames are already finished pixels: no tone mapping on save
sc.view_settings.look = "None"

out = []
for h in roster:
    d = os.path.join(SRC, h["id"])
    files = sorted(glob.glob(os.path.join(d, "gallop_[0-9][0-9].png")))
    if not files:
        print("SKIP (not rendered)", h["id"])
        continue
    anchors = json.load(open(os.path.join(d, "anchors.json"), encoding="utf-8"))
    fw, fh, n = anchors["w"], anchors["h"], len(files)
    rows = (n + COLS - 1) // COLS
    sheet = np.zeros((fh * rows, fw * COLS, 4), dtype=np.float32)
    for i, fp in enumerate(files):
        im = bpy.data.images.load(fp)
        im.colorspace_settings.name = "sRGB"
        px = np.array(im.pixels[:], dtype=np.float32).reshape(fh, fw, 4)
        r, c = divmod(i, COLS)
        y0 = fh * rows - (r + 1) * fh                  # Blender images are stored bottom row first
        sheet[y0:y0 + fh, c * fw:(c + 1) * fw] = px
        bpy.data.images.remove(im)
    img = bpy.data.images.new(f"sheet_{h['id']}", fw * COLS, fh * rows, alpha=True)
    img.colorspace_settings.name = "sRGB"
    img.pixels[:] = sheet.ravel()
    path = os.path.join(DST, "hounds", f"{h['id']}.webp")
    img.save_render(path, scene=sc)
    bpy.data.images.remove(img)
    entry = {k: h.get(k) for k in ("id", "name", "short", "note", "bio", "hue", "sat", "cadence", "fx") if h.get(k) is not None}
    entry["sheet"] = {"file": f"hounds/{h['id']}.webp", "cols": COLS, "rows": rows, "w": fw, "h": fh, "frames": n}
    entry["anchors"] = {"ground": anchors["ground"], "stride": anchors["stride"], "frames": anchors["frames"]}
    out.append(entry)
    print("SHEET", h["id"], n, "frames", os.path.getsize(path) // 1024, "KB")

# the crowd (scripts/render_crowd.py renders it next to the gallops): one sheet per monster, a row per move
CROWD = os.path.join(os.path.dirname(os.path.normpath(SRC)), "crowd")
people, moves = [], None
for cid in sorted(os.listdir(CROWD)) if os.path.isdir(CROWD) else []:
    d = os.path.join(CROWD, cid)
    if not os.path.isfile(os.path.join(d, "anchors.json")):
        continue
    an = json.load(open(os.path.join(d, "anchors.json"), encoding="utf-8"))
    fw, fh, moves = an["w"], an["h"], an["anims"]
    cols = max(n for _, n, _ in moves)
    sheet = np.zeros((fh * len(moves), fw * cols, 4), dtype=np.float32)
    for r, (name, n, _) in enumerate(moves):
        for i in range(n):
            im = bpy.data.images.load(os.path.join(d, f"{name}_{i}.png"))
            im.colorspace_settings.name = "sRGB"
            px = np.array(im.pixels[:], dtype=np.float32).reshape(fh, fw, 4)
            y0 = fh * len(moves) - (r + 1) * fh
            sheet[y0:y0 + fh, i * fw:(i + 1) * fw] = px
            bpy.data.images.remove(im)
    os.makedirs(os.path.join(DST, "crowd"), exist_ok=True)
    img = bpy.data.images.new(f"crowd_{cid}", fw * cols, fh * len(moves), alpha=True)
    img.colorspace_settings.name = "sRGB"
    img.pixels[:] = sheet.ravel()
    path = os.path.join(DST, "crowd", f"{cid}.webp")
    img.save_render(path, scene=sc)
    bpy.data.images.remove(img)
    people.append({"id": cid, "file": f"crowd/{cid}.webp", "ground": an["ground"], "hips": an["hips"], "top": an["top"],
                   "hand": an["hand"]})
    print("CROWD", cid, os.path.getsize(path) // 1024, "KB")
if people:
    with open(os.path.join(DST, "derby_crowd.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"_about": "Hellhound Derby crowd, written by art/derby/scripts/export_assets.py from art/derby/out/crowd",
                   "w": fw, "h": fh, "release": an["release"], "moves": moves, "people": people}, f, separators=(",", ":"))

with open(os.path.join(DST, "derby_hounds.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump({"_about": "Hellhound Derby roster, written by art/derby/scripts/export_assets.py - edit art/derby/hounds.json and re-export",
               "hounds": out}, f, separators=(",", ":"), ensure_ascii=False)
with open(os.path.join(DST, "derby_lines.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(lines, f, indent=1, ensure_ascii=False)
print("ROSTER", len(out), "hounds ->", DST)
