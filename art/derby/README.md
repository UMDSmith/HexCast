# Hellhound Derby art

Source art and the scripts that turn it into the hound sprite sheets for the Hellhound Derby game
add-on. **Nothing in this folder ships.** Only the finished sprite sheets do, inside
`catalog/games_derby/static/` (written by `scripts/export_assets.py`). Streamers never need Blender:
they get the rendered frames.

```
art/derby/
  README.md              this file: sources, licences, how to render
  hounds.json            the art roster: names, bio (the line on the lineup), gait personality, colours, effects
  lines.json             what the hounds say (trash talk, reactions, the commentary)
  scripts/
    houndlib.py          load + light the model, the procedural poser, the gallop, extra heads, tints
    harry.py             Hungry Harry's skeleton
    render_gallop.py     render every hound's gallop loop (+ anchor points) and the preview pages
    build_preview.py     rebuild the preview pages only (after a colour/effect/race change)
    export_assets.py     pack the renders into the add-on: hounds/<id>.webp + derby_hounds.json + derby_lines.json
    fx.js                the overlay effects (fire prints, slime, lightning prints...), shared by both pages
    preview_template.html  every hound on its own strip, with an effect picker
    race_template.html   the race prototype: one shared track, a scripted race with antics and trash talk
  source/                downloaded models (git-ignored; get them from the links below)
  out/                   renders (git-ignored)
```

## Models and licences

| Used for | Model | Author | Licence | Where |
| --- | --- | --- | --- | --- |
| every hound | **Hell Hound** (`cerber1.fbx`, Rigify rig, baked idle/walk/attack/aggro) | Adam (Sketchfab: shadow798) | CC BY (Sketchfab "CC Attribution"): credit required, commercial use allowed | <https://sketchfab.com/3d-models/hell-hound-36f3f384fd9b41a9a8dae1aaf22349d3> |
| Hungry Harry's skull | **Dog (Canis familiaris) Dolichcephalic** (`Canine Skull.stl`, a CT scan) | The University of Edinburgh Open.Ed; CT courtesy of Dr. Tobias Schwarz, prepared by Brian Mather, The Royal (Dick) School of Veterinary Studies | CC BY | <https://sketchfab.com/3d-models/5d8d86a270b64f05aab45c3d09f350a6> |

Both credits must appear in the add-on (its help page and a credits file next to the sprites).

Put the downloads in `source/` and unzip each into its own folder:
`source/hell-hound/source/cerber1.fbx`, `source/edinburgh-dog-skull/source/Canine Skull.stl`.

Open question: the Hell Hound's eye mesh is named "Red Blackout Stylised", which may be a third-party
eye asset. Replacing the eyes with our own would settle it.

### Looked at and rejected

Check where a "free" model really comes from before using it. These were labelled CC BY but are not:

- **"Animated Wolf Scene" by Roo**: its `.blend` carries 3DHaupt's own readme ("I modeled, textured and
  rigged this wolf…"). It is a re-upload of 3DHaupt's wolf, which he licenses **CC BY-NC-SA** (no
  commercial use).
- **"Cerberus" by Alexey (lexazaixa25)**: matches the paid Unity Asset Store package "Cerberus 1.0"
  exactly (21.8k triangles, 20 animations of which 5 are root-motion variants, fire breath, 4096
  textures, `CERBERUS@clip.FBX` naming), and has no description and no other uploads. A re-upload of a
  paid asset.
- "Animated Wolf" by emptypen2477 (98 animations, no description), "Dog Skeleton" by PratyushParag
  (copy-pasted "iPhone case" description), AI-generated skeleton sculptures: provenance unclear.
- Anything NonCommercial or NoDerivs.

## Rendering

Blender 5.2+ (only to make the art). From the repo root:

```
blender -b -Y -P art/derby/scripts/render_gallop.py -- <abs path>/art/derby/source/hell-hound/source/cerber1.fbx <abs path>/art/derby/out/gallop 16
```

Optional last argument: a comma-separated list of hound ids to render only those. Open
`out/gallop/preview.html` to watch the loops on a scrolling track. `-Y` keeps scripts embedded in
downloaded `.blend` files from running.

- Side-on, orthographic, transparent background; the dog faces -Y in the model, so the camera sits at -X
  and the hound runs left to right.
- Lights: warm key from the camera side, orange rim from behind, a lava bounce from below, a cold fill.

Then copy them into the add-on (one 8×2 WebP sheet per hound, about 1 MB for all nine, plus the roster
and the lines):

```
blender -b -Y -P art/derby/scripts/export_assets.py -- <abs path>/art/derby/out/gallop <abs path>/catalog/games_derby/static
```

After editing only `hounds.json` names/bios or `lines.json`, the same export is all it takes (no re-render).

## How the posing works

The model's rig is a Rigify export (423 bones); only the `DEF-*` bones deform the mesh. `houndlib.Poser`
poses them from planar joint angles (rotations about the side-to-side axis), composed down a "virtual"
hierarchy of DEF bones (an `ORG-x` ancestor stands for `DEF-x`), then converts the result to pose-bone
values. Things that bit:

- **Connected bones.** Many face bones are connected to MCH/ORG helpers that are never posed, so Blender
  ignores their location and they stay behind when the head moves (the eyes and eyelids became a
  stretched orange spike). `load()` disconnects every bone; the rest pose is unchanged.
- **The tail chain is reversed** (`DEF-spine` is the tail tip, `DEF-spine.003` the base), so it is
  re-hung from the hips and turns about each bone's body end.
- **Ears**: Rigify ear bones form a loop and some hang off the head, so each ear's bones turn together
  about the ear base.
- Sun lights shine along their local -Z axis.

## The special hounds

- **Three-Heads Terry** (`gait.heads` in `hounds.json`): `houndlib.add_heads` copies the head and upper-neck
  bones (names get `~2`, `~3`) and the head/neck mesh and eyes onto them; `head_offsets` fans the copies
  out (one lower and nearer the camera, one raised and set back) and each bobs and snaps on its own beat.
  `tint` bakes per-part colour into the render (his red body, one head yellower, one darker).
- **Hungry Harry** (`build: skeleton`, `scripts/harry.py`): the flesh is hidden and every bone is a rigid
  generated piece on one DEF bone (vertebrae, ribs traced from the torso's cross-section, pelvis, shoulder
  blades, leg bones, toes and claws), plus the Edinburgh skull scan and a generated lower jaw.

## Effects and anchor points

Effects (fire paw prints, slime, lightning prints, soul-fire...) are drawn by the overlay, not baked
into the frames, so trails stay on the track and can be recoloured per hound. For that the renderer
writes `out/<hound>/anchors.json`: for every frame, where the paws (with an on-the-ground flag), tail,
mouth, eye, head, back and hips are in the image, plus the ground row and the stride (how far the
track moves per loop, so planted paws don't slide). `scripts/build_preview.py` rebuilds the preview page
after a colour or effect change without re-rendering.

## The gallop

A rotary gallop: left hind, right hind, right front, left front, each landing on its own beat. The
body is held in one long line from nose to tail, the muzzle level, the front legs reaching nearly
horizontal and the hind legs driving far back, straightening as they push off. Every number is in
`houndlib.GALLOP`; each hound in `hounds.json` overrides some of them (head carriage, ears, tail, leap,
reach, back flex, jaw, a limp, size), which gives them their own way of running.

`houndlib.cycle` keeps planted paws on the ground: the body rides a smooth height (with a leap in the
gathered flight), each planted paw is solved onto a spot that slides back at one shared ground speed
(two unknowns: hip angle and a knee/wrist bend), and a swinging paw that would scrape the ground folds
(or, reaching forward, reaches higher). The rig hangs the shoulders on the base of the neck; `Poser`
re-hangs them on the chest, or lowering the head would swing the front legs down with it.
