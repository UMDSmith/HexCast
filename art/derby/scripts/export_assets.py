"""Copy the renders into the add-on: WebP sprite sheets for the hounds, the crowd and the junk, plus their files.

    blender -b -Y -P art/derby/scripts/export_assets.py -- <abs path>/art/derby/out/gallop <abs path>/catalog/games_derby/static
            [--local <local spec.json> <pack folder>]

Writes, under the add-on's static/ folder:
  hounds/<id>.webp      the gallop loop: frames in a COLS x ROWS grid, left to right, top to bottom
  derby_hounds.json     the roster (art/derby/hounds.json: names, colours, effects) + each hound's sheet
                        and anchor points (art/derby/out/gallop/<id>/anchors.json)
  derby_lines.json      what they say (art/derby/lines.json)
  crowd/<id>.webp       a monster in the stands: a row per move (out/crowd, scripts/render_crowd.py)
  derby_crowd.json      the crowd: the moves, each monster's feet, hips and throwing hand
  props/<id>.webp       a piece of junk: its tumble, then how it lies (out/props, scripts/render_props.py)
  derby_props.json      the junk: names (= JUNK in derby.py), sizes, sheets
  guests/<id>.webp      a guest racer running (out/guests, scripts/render_guest.py)
  derby_guests.json     the guests: names, lines, how they run (art/derby/guests.json), sheets and anchors
  derby_credits.json    who made the models (art/derby/credits.json + each packed prop's credit): the Credits
                        card at the bottom of the Derby tab
  CREDITS.md            the same, as a file next to the sprites
The crowd, the junk and the guests are packed when they have been rendered (next to out/gallop). The server reads
the roster (ids, names), the guests and the lines; the overlay reads all of it.

--local packs the guests of a local spec (art/derby/source/guests/local.json: never part of the add-on) into a
folder instead: guests.json and their sheets, ready to drop into a Hexcast's config/games_derby_guests/.
"""
import bpy, sys, os, json, glob
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[sys.argv.index("--") + 1:]
SRC, DST = argv[0], argv[1]
LOCAL = argv[argv.index("--local") + 1:argv.index("--local") + 3] if "--local" in argv else None
OUT = os.path.dirname(os.path.normpath(SRC))
COLS = 8
QUALITY = 82
PROP_BASE = 128         # the junk: how many px a frame is drawn at, at scale 1 and size 1
LICENCES = {"CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/", "CC BY 3.0": "https://creativecommons.org/licenses/by/3.0/",
            "CC0": "https://creativecommons.org/publicdomain/zero/1.0/"}

roster = json.load(open(os.path.join(HERE, "..", "hounds.json"), encoding="utf-8"))["hounds"]
lines = json.load(open(os.path.join(HERE, "..", "lines.json"), encoding="utf-8"))
sc = bpy.context.scene
s = sc.render.image_settings
s.file_format = "WEBP"; s.color_mode = "RGBA"; s.quality = QUALITY
sc.view_settings.view_transform = "Standard"      # the frames are already finished pixels: no tone mapping on save
sc.view_settings.look = "None"


def pack(rows, fw, fh, cols, path):
    """rows: a list of rows of frame files; one sheet, frame (r, c) at column c of row r. Returns its KB."""
    sheet = np.zeros((fh * len(rows), fw * cols, 4), dtype=np.float32)
    for r, files in enumerate(rows):
        for c, fp in enumerate(files):
            im = bpy.data.images.load(fp)
            im.colorspace_settings.name = "sRGB"
            px = np.array(im.pixels[:], dtype=np.float32).reshape(fh, fw, 4)
            y0 = fh * len(rows) - (r + 1) * fh                  # Blender images are stored bottom row first
            sheet[y0:y0 + fh, c * fw:(c + 1) * fw] = px
            bpy.data.images.remove(im)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = bpy.data.images.new("sheet", fw * cols, fh * len(rows), alpha=True)
    img.colorspace_settings.name = "sRGB"
    img.pixels[:] = sheet.ravel()
    img.save_render(path, scene=sc)
    bpy.data.images.remove(img)
    return os.path.getsize(path) // 1024


def grid(files, cols):
    return [files[i:i + cols] for i in range(0, len(files), cols)]


def write(name, data, **kw):
    with open(os.path.join(DST, name), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, **(kw or {"separators": (",", ":")}))


# the hounds
out = []
for h in roster:
    d = os.path.join(SRC, h["id"])
    files = sorted(glob.glob(os.path.join(d, "gallop_[0-9][0-9].png")))
    if not files:
        print("SKIP (not rendered)", h["id"])
        continue
    anchors = json.load(open(os.path.join(d, "anchors.json"), encoding="utf-8"))
    fw, fh, n = anchors["w"], anchors["h"], len(files)
    kb = pack(grid(files, COLS), fw, fh, COLS, os.path.join(DST, "hounds", f"{h['id']}.webp"))
    entry = {k: h.get(k) for k in ("id", "name", "short", "note", "bio", "hue", "sat", "cadence", "fx") if h.get(k) is not None}
    entry["sheet"] = {"file": f"hounds/{h['id']}.webp", "cols": COLS, "rows": (n + COLS - 1) // COLS, "w": fw, "h": fh, "frames": n}
    entry["anchors"] = {"ground": anchors["ground"], "stride": anchors["stride"], "frames": anchors["frames"]}
    out.append(entry)
    print("SHEET", h["id"], n, "frames", kb, "KB")

# the crowd: one sheet per monster, a row per move
CROWD = os.path.join(OUT, "crowd")
people = []
for cid in sorted(os.listdir(CROWD)) if os.path.isdir(CROWD) else []:
    d = os.path.join(CROWD, cid)
    if not os.path.isfile(os.path.join(d, "anchors.json")):
        continue
    an = json.load(open(os.path.join(d, "anchors.json"), encoding="utf-8"))
    moves = an["anims"]
    rows = [[os.path.join(d, f"{name}_{i}.png") for i in range(n)] for name, n, _ in moves]
    kb = pack(rows, an["w"], an["h"], max(n for _, n, _ in moves), os.path.join(DST, "crowd", f"{cid}.webp"))
    people.append({"id": cid, "file": f"crowd/{cid}.webp", "ground": an["ground"], "hips": an["hips"], "top": an["top"],
                   "hand": an["hand"]})
    print("CROWD", cid, kb, "KB")
if people:
    write("derby_crowd.json", {"_about": "Hellhound Derby crowd, written by art/derby/scripts/export_assets.py from art/derby/out/crowd",
                               "w": an["w"], "h": an["h"], "release": an["release"], "moves": moves, "people": people})

# the junk: one sheet per prop, its tumble and then how it lies once it has landed
PROPS = os.path.join(OUT, "props")
props, credits = [], json.load(open(os.path.join(HERE, "..", "credits.json"), encoding="utf-8"))["credits"]
for spec in json.load(open(os.path.join(HERE, "..", "props.json"), encoding="utf-8"))["props"]:
    d = os.path.join(PROPS, spec["id"])
    if not os.path.isfile(os.path.join(d, "meta.json")):
        print("SKIP (not rendered)", spec["id"])
        continue
    pm = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    files = [os.path.join(d, f"tumble_{i:02d}.png") for i in range(pm["frames"])] + [os.path.join(d, "rest.png")]
    pcols = (len(files) + 1) // 2
    kb = pack(grid(files, pcols), pm["w"], pm["h"], pcols, os.path.join(DST, "props", f"{spec['id']}.webp"))
    props.append({"id": spec["id"], "name": spec["name"], "file": f"props/{spec['id']}.webp", "size": spec.get("size", 1),
                  "rest_bottom": pm["rest_bottom"]})
    if spec.get("credit"):
        credits.append(dict(spec["credit"], **{"for": "Junk: " + spec["name"].lower()}))
    print("PROP", spec["id"], kb, "KB")
# the guests: the add-on's (art/derby/guests.json), and a local pack's when asked
GUESTS = os.path.join(OUT, "guests")


def guests(spec_path, dst, prefix):
    """pack the rendered guests of a spec into dst; returns their entries (and adds an add-on guest's credit)."""
    out = []
    for g in json.load(open(spec_path, encoding="utf-8"))["guests"]:
        d = os.path.join(GUESTS, g["id"])
        if not os.path.isfile(os.path.join(d, "anchors.json")):
            print("SKIP (not rendered)", g["id"]); continue
        an = json.load(open(os.path.join(d, "anchors.json"), encoding="utf-8"))
        files = sorted(glob.glob(os.path.join(d, "run_[0-9][0-9].png")))
        kb = pack(grid(files, COLS), an["w"], an["h"], COLS, os.path.join(dst, f"{prefix}{g['id']}.webp"))
        entry = {k: g[k] for k in ("id", "name", "short", "bio", "wobble", "lines", "hue", "sat", "cadence") if k in g}
        entry["sheet"] = {"file": f"{prefix}{g['id']}.webp", "cols": COLS, "rows": (len(files) + COLS - 1) // COLS,
                          "w": an["w"], "h": an["h"], "frames": len(files)}
        entry["anchors"] = {k: an[k] for k in ("ground", "stride", "nose", "body", "frames")}
        out.append(entry)
        if g.get("credit") and prefix:
            credits.append(dict(g["credit"], **{"for": "Guest racer: " + g["name"]}))
        print("GUEST", g["id"], len(files), "frames", kb, "KB")
    return out


shipped = guests(os.path.join(HERE, "..", "guests.json"), DST, "guests/") if os.path.isfile(os.path.join(HERE, "..", "guests.json")) else []
write("derby_guests.json", {"_about": "Hellhound Derby guest racers, written by art/derby/scripts/export_assets.py from art/derby/guests.json "
                            "and out/guests. Your own go in config/games_derby_guests/ (the same format).", "guests": shipped})
if LOCAL:
    os.makedirs(LOCAL[1], exist_ok=True)
    pack_ = guests(LOCAL[0], LOCAL[1], "")
    with open(os.path.join(LOCAL[1], "guests.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"_about": "A local Hellhound Derby guest pack (not part of the add-on): copy this folder to a Hexcast's "
                   "config/games_derby_guests/.", "guests": pack_}, f, ensure_ascii=False, indent=1)
    print("LOCAL PACK", len(pack_), "guests ->", LOCAL[1])

about = "Hellhound Derby junk, written by art/derby/scripts/export_assets.py from art/derby/props.json and out/props"
write("derby_props.json", {"_about": about, "w": pm["w"], "h": pm["h"], "frames": pm["frames"], "cols": pcols, "base": PROP_BASE,
                           "props": props} if props else {"_about": about, "props": []})     # (none yet: the overlay draws the junk)

# the credits: the card at the bottom of the Derby tab, and CREDITS.md
for c in credits:
    if not c.get("licence_url") and c.get("licence") in LICENCES:
        c["licence_url"] = LICENCES[c["licence"]]
write("derby_credits.json", {"_about": "Hellhound Derby art credits, written by art/derby/scripts/export_assets.py from art/derby/credits.json and props.json",
                             "credits": credits})
md = ["# Hellhound Derby: art credits", "",
      "The sprites in `hounds/`, `crowd/` and `props/` are rendered from these models (in Blender: see `art/derby/` in the",
      "Hexcast repository). The same list is the Credits card at the bottom of the Derby tab. This file is written by",
      "`art/derby/scripts/export_assets.py` from `art/derby/credits.json` and `art/derby/props.json`.", ""]
for c in credits:
    md += [f"- **{c['for']}**: \"{c['title']}\" by {c['author']}, {c['licence']}" + (f" ({c['licence_url']})" if c.get("licence_url") else ""),
           f"  {c['url']}"] + ([f"  {c['changes']}"] if c.get("changes") else []) + [""]
md += ["Everything else was made for Hexcast: Hungry Harry's other bones, the effects (fire, slime, lightning, sulfur,",
       "flies, soul fire), the track, the stands, any junk drawn without a model, the sounds and the dialogue.", ""]
with open(os.path.join(DST, "CREDITS.md"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(md))
print("CREDITS", len(credits))

write("derby_hounds.json", {"_about": "Hellhound Derby roster, written by art/derby/scripts/export_assets.py - edit art/derby/hounds.json and re-export",
                            "hounds": out})
write("derby_lines.json", lines, indent=1)
print("ROSTER", len(out), "hounds ->", DST)
