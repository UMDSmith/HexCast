# Hellhound Derby art

Source art and the scripts that turn it into the hound, crowd, junk and guest sprite sheets for the Hellhound Derby
game add-on. **Nothing in this folder ships.** Only the finished sprite sheets do, inside
`catalog/games_derby/static/` (written by `scripts/export_assets.py`). Streamers never need Blender:
they get the rendered frames.

```
art/derby/
  README.md              this file: sources, licences, how to render
  hounds.json            the art roster: names, bio (the line on the lineup), gait personality, colours, effects
  lines.json             what the hounds say (trash talk, reactions, the commentary)
  props.json             the crowd's junk: names, sizes, how each lies when it lands, dirt and blood, credits
  credits.json           who made the models (the junk's credits are in props.json, the guests' in guests.json)
  guests.json            the guest racers that ship: names, lines, how they run, credits (original characters only)
  scripts/
    houndlib.py          load + light the model, the procedural poser, the gallop, extra heads, tints
    harry.py             Hungry Harry's skeleton
    render_gallop.py     render every hound's gallop loop (+ anchor points) and the preview pages
    crowdlib.py          the crowd: load a Mixamo character, pose it (idle, cheer, pump, jeer, clap, throw), light it
    render_crowd.py      render every crowd monster's moves (+ feet, hips, throwing hand)
    render_props.py      render every piece of junk tumbling (+ how it lies once it lands)
    guestlib.py          the guests: find any humanoid skeleton's joints, a run, a centaur's gallop, fix half-skinned models
    render_guest.py      render a guests file's racers running (+ feet, head, hips, nose)
    build_preview.py     rebuild the preview pages only (after a colour/effect/race change)
    export_assets.py     pack the renders into the add-on: hounds/<id>.webp, crowd/<id>.webp, props/<id>.webp,
                         guests/<id>.webp, derby_hounds.json, derby_crowd.json, derby_props.json, derby_guests.json,
                         derby_lines.json, derby_credits.json; --local packs a local guest pack instead
    fx.js                the overlay effects (fire prints, slime, lightning prints...), shared by both pages
    preview_template.html  every hound on its own strip, with an effect picker
    race_template.html   the race prototype: one shared track, a scripted race with antics and trash talk
  source/                downloaded models (git-ignored; get them from the links below); source/guests/local.json is
                         our own local guest pack's spec (never committed)
  out/                   renders (git-ignored)
```

## Models and licences

| Used for | Model | Author | Licence | Where |
| --- | --- | --- | --- | --- |
| every hound | **Hell Hound** (`cerber1.fbx`, Rigify rig, baked idle/walk/attack/aggro) | Adam (Sketchfab: shadow798) | CC BY (Sketchfab "CC Attribution"): credit required, commercial use allowed | <https://sketchfab.com/3d-models/hell-hound-36f3f384fd9b41a9a8dae1aaf22349d3> |
| Hungry Harry's skull | **Dog (Canis familiaris) Dolichcephalic** (`Canine Skull.stl`, a CT scan) | The University of Edinburgh Open.Ed; CT courtesy of Dr. Tobias Schwarz, prepared by Brian Mather, The Royal (Dick) School of Veterinary Studies | CC BY | <https://sketchfab.com/3d-models/5d8d86a270b64f05aab45c3d09f350a6> |

| the crowd | **Mixamo** characters (12: Warrok, Maw, Mutant, Goblin, Nightshade, Parasite, Pumpkinhulk, Vampire, Warzombie, Yaku, Ch25, Ch30), T-pose FBX, no animation | Adobe | Mixamo terms: royalty free for personal, commercial and non-profit projects; the raw character files must not be redistributed | <https://www.mixamo.com> (free Adobe account) |
| the guest racer Hamageddon | **Pig Demon** (glTF, 3ds Max Biped rig, an idle) | Lexington Dath | CC BY 4.0 | <https://sketchfab.com/3d-models/pig-demon-bba636efaee04b3ea988b2ae4487cfc1> |
| the guest racer Skullduggery (`source/guests/skull/`) | **Skull Character** (glTF, its own Run: two strides in frames 0-64) | Oxixo | CC BY 4.0 | <https://sketchfab.com/3d-models/skull-character-40897a5169ca4fdcb826a5e388d49f2f> |
| the guest racer The Cockfather (`chicken/`) | **Legendary Chicken Warrior (Animated, 35 motions)** (glTF, Character Creator rig, `RPG_Armed_Run` moves it along) | Jungle Jim | CC BY 4.0 | <https://sketchfab.com/3d-models/legendary-chicken-warrior-animated-35-motions-4c7deda095d64f8cb0e4b8c5277d1f19> |
| the guest racer Totally A Barrel (`mimic/`) | **Creature Mimic** (glTF, crawls on its hands; its moves come out torn unless imported with `guess_original_bind_pose` off) | Parado10 | CC BY 4.0 | <https://sketchfab.com/3d-models/creature-mimic-f6821270b6b443789468090212a5c963> |
| the guest racer Moo-tilator (`minotaur/`) | **Minotaur Berserker - Free Game-Ready Character** (glTF, Mixamo rig, a walk) | Yury Misiyuk (Sketchfab: Tim0) | CC BY 4.0 | <https://sketchfab.com/3d-models/minotaur-berserker-free-game-ready-character-42da47ae59574ed5a6b86b49734294cc> |
| the guest racer Lord Horny (`helldemon/`) | **HellDemon Reborn** (glTF, a flat rig: every DEF bone on the root; `guess_original_bind_pose` off too) | ArtistForge16 (Sketchfab: danielcr677) | CC BY 4.0 | <https://sketchfab.com/3d-models/helldemon-reborn-43e163c12f304a35b1f85476ca2549f6> |

Every model needs its credit in the add-on: put it in `credits.json` (a piece of junk: `credit` in `props.json`).
The export writes them to `static/derby_credits.json` (the **Credits** card at the bottom of the Derby tab) and
`static/CREDITS.md`; a junk credit goes in only once that junk is rendered and packed.
The Mixamo files never go in the repo or the add-on, only the rendered frames.

Put the downloads in `source/` and unzip each into its own folder:
`source/hell-hound/source/cerber1.fbx`, `source/edinburgh-dog-skull/source/Canine Skull.stl`. Each crowd
monster gets its own folder named by its id: `source/crowd/warrok/Warrok W Kurniawan.fbx` (Mixamo:
Characters, pick one, Download as FBX Binary, T-pose, With Skin).

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
The export also packs the crowd if `out/crowd/` has been rendered:

```
blender -b -Y -P art/derby/scripts/render_crowd.py -- <abs path>/art/derby/source/crowd <abs path>/art/derby/out/crowd
```

(optional last argument: crowd ids). One 960×840 sheet per monster (a row per move, 8 frames of 120×140),
about 1.2 MB for all twelve. `out/crowd/<id>/sheet.png` is a contact sheet to check the moves.

## The junk

Every thing the crowd throws is a model in `source/props/<id>/` (any .glb, .gltf, .fbx, .obj, .stl or
`.blend`), listed in `props.json` under the name the server and the commentary use (`JUNK` in `derby.py`).

```
blender -b -P art/derby/scripts/render_props.py -- <abs path>/art/derby/source/props <abs path>/art/derby/out/props [ids]
```

(No `-Y`: a downloaded `.blend` can carry scripts, and nothing here needs them.) Each model is laid down
(`rot`, Euler degrees; the camera looks along +X, so x points away from it, -y to the right and z up),
centred and scaled to a diagonal of 1 (big scans are decimated), dirtied or bloodied (`grime`, `blood`,
`tint`; a mug gets a `liquid` surface, and `lid` drops a tankard's lid so the blood shows), and rendered
with the hounds' lights turned up: 16 frames of one tumble (end over end, with a wobble out of the screen)
and a `rest` frame for when it has landed, 128 px square. Unlit scans (Sketchfab's "shadeless" ones: the
femur, the hand) get a plain lit surface first. The export packs them (15–50 KB each); the overlay picks
the frame from the junk's spin and draws it at `size` (128 px at size 1 and scale 1). Anything without a
sprite is drawn as before. All 14 come from Sketchfab (CC BY 4.0) and Poly Haven (CC0); the credits are in
`props.json`.

A new piece of junk needs its name in `JUNK` (`derby.py`) and `JUNK_NAMES` (`derby.js`), an entry in
`props.json`, and its lines in `lines.json`: `thrown.<NAME>` (the hound it hit or nearly hit) and
`eaten.<NAME>` (Bubba). Pick a name that reads after "A" ("A WOOD AXE", not "A AXE"): the commentary says
"A {o} TO {a}'S HEAD!".

## The guests

A guest racer is a rigged model in `source/guests/<model>/` (a .glb, .gltf or .fbx), listed in a guests file:
`guests.json` for the ones that ship (original characters under CC BY / CC0 only), or a local pack's spec
(`source/guests/local.json`, git-ignored) for anything that can't ship.

```
blender -b -P art/derby/scripts/render_guest.py -- <abs path>/art/derby/guests.json <abs path>/art/derby/source/guests <abs path>/art/derby/out/guests
blender -b -P art/derby/scripts/render_guest.py -- <abs path>/art/derby/source/guests/local.json <abs path>/art/derby/source/guests <abs path>/art/derby/out/guests
blender -b -P art/derby/scripts/export_assets.py -- <abs path>/art/derby/out/gallop <abs path>/catalog/games_derby/static --local <abs path>/art/derby/source/guests/local.json <abs path>/config/games_derby_guests
```

(No `-Y`: downloads can carry scripts.) The models come rigged every which way, so `guestlib` first makes what
you see the rest pose (it bakes each skinned mesh as it stands and applies the pose as the rest: imported rigs
often stand in something else, even at another scale), then finds the joints by name: Mixamo, Uma-style game rips
(`Hip`, `Thigh_L`, `Ankle_offset_L`...), MMD (`Left leg`...) and 3ds Max Biped (`Bip001 L Thigh`...) are known,
`joints` in the spec fills in the rest. The run is made, like the crowd's moves: 16 frames of one stride, the feet
on a running path (two-bone IK, knees forward), the arms pumping against the legs (bent from the upper arm), a lean
and a bounce (`run`: lean in the spine, hip (the pelvis tipping in too), stride, stance, lift, kick, bounce, drop,
arm, elbow, arm_lean (how much of the lean the arms follow), nod). A sprinter leans: about 35 degrees all told
(lean + hip), or it looks like it's out for a stroll. A centaur (`gait: centaur`, `legs`, `hips`, `head`, `torso`,
`arms`) gallops instead: four legs on a rotary gallop's footfalls, swinging from the shoulder and hip, the front
knees and rear hocks folding in the swing, the body pitching, the rider half leaning in, its arms pumping (`gallop`:
torso, lean for the head, arm). A long cloak hides the legs, so let the wind take it: its skirt bones go in `trail`
like a tail.
Loose parts stream behind (`trail`: {bone-name pattern: [share, lift, wave]}): each bone of a tail, a ponytail or a
ribbon turns `share` of the way toward the wind (behind, `lift` degrees above level) and the chain waves twice a
stride. Without it, `guestlib.TRAIL` takes a game rip's swing bones: `Sp_Hi_Tail*` (the tail), `Sp_He_Hair1-9*`
(the hair, not the bangs) and the jacket tails and ribbons. A model with no swing bones (most) keeps them still,
and a part stuck rigidly to the head can't stream at all. A model that came half
skinned (a static body over a skinned one, every face expression as its own mesh) is fixed in the spec: `hide`
parts, `attach` parts rigidly to a bone (attach things to what they hang on: a sword on a centaur's flank goes on
its horse spine, not its rider), `skin` parts with automatic weights from the listed bones only (`upper`: above a
height, and with `y_max` only in front of it, only those bones, so a centaur's hands don't ride its knees; without
`y_max` the cut took its rump too, and leaning the rider levered the rump up out of the cloak), and `squeeze` a body
under a cloak sideways (`by`: a share, fading in over `z` and `y` so the rider isn't touched): seen only from the
side nothing shows, and the bulges of a gallop (body and cloak are skinned differently) stay under the cloak.
(Pushing it in along its normals folds it and pokes out somewhere else.) To find what pokes through, render each
motion alone (zero the rest of `gallop`), and hide one part at a time.

A model that comes with a run of its own plays it instead (`anim`: `{"action": its name, "frames": [from, to]}`,
one stride: both feet once; check the action's length, some hold two). It keeps its rest as it came, up is the
world's, and a run that carries it along (root motion) is held in place: the stride is as far as the run moved it,
or else as far back as a planted foot slides. `joints` then only needs the hips, head and legs, and anything that
moves on two "feet" will do (Totally A Barrel's are its hands). Its lowest point over the stride is the ground. A
walk works too, but the overlay steps the frames by the distance run, so a walk's short stride plays far too fast
at race speed (Totally A Barrel's 63 px: 11 strides a second, a flicker): give it a longer `stride` (px; 170 plays
about 4 a second) and it skates a little instead. Two import traps: a rig whose moves
come out torn into spikes (or whose rest stands in pieces) wants `import: {"guess_original_bind_pose": false}`
(the importer's options); a flat rig (every deform bone on the root, moved by controls the export dropped) needs
its chain back before it can be given a run: `parents` (`{bone: parent}`, the bone may be a pattern:
`"DEF-ARM\\.(L|R)": "DEF-SHOULDER.\\1"`), including any control bone something hangs on (Lord Horny's jaw and
claws). `lights` turns the hounds' lights up for a dark model (`{"key": 2, "fill": 3}`: key, rim, lava, fill), and
`x` is where the hips sit across the frame (0.5; a long tail behind wants more). The face anchor (`mouth`, which
also gives the `nose` the finish line goes by) is the frontmost point of the mesh the head moves: a beak's or a
snout's tip.

The frames are `w` x `h` px with the guest `height` px tall standing, lit like the hounds and seen the way they are
(running to the right). The anchors give the overlay what a hound's do: the ground, the stride (how far one cycle
carries it, so the feet don't skate), the nose (the finish line goes by it), the head and mouth (its name tag, the
photo finish), the hips and backside (a flaming arse) and the feet (down or up). The export packs the add-on's into
`static/guests/` + `derby_guests.json` (their credits go on the Credits card) and, with `--local`, a local pack's
into a folder: `guests.json` and the sheets, ready for any Hexcast's `config/games_derby_guests/`.

## The crowd

The Mixamo downloads are characters only, in the T-pose. `crowdlib.Rig` poses any Mixamo skeleton
(`mixamorig:` bones) with one set of moves: limbs are aimed at directions given in the character's own
frame (out, forward, up), the spine, head, jaw and fingers turn relative to their parent, and the legs keep
the feet planted with a two-bone IK when the hips bounce. So a new monster needs no animation work: drop its
FBX in `source/crowd/<id>/` and render. The moves: `idle`, `cheer` (both fists up, bouncing), `pump`
(one fist), `jeer` (leaning over the rail, fists shaking, mouth open), `clap`, and an overhand `throw`
whose junk leaves the hand on frame 4 (`release`; the hand's place is in the anchors).

Front on, orthographic, 44 px per metre (a 1.8 m monster is about 80 px tall), turned 15° toward where
the hounds go; the overlay mirrors half the crowd, shrinks the back tiers, darkens them, and tints the
lot to the theme. Same lights as the hounds, from the stands' side.

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
