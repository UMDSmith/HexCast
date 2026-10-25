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
    probe_guest.py       look inside guest models: bones, the joints found and missing, clips, size
    render_morph.py      a guest that changes shape partway through a race (a robot into its truck): the change,
                         once, and the new shape running, from the file's own animation of it
    import_sprite.py     a guest painted in 2D: a PNG run cycle cut out and measured into what render_guest.py
                         writes (frames + anchors; plain Python + Pillow, numpy, scipy)
    fetch_guests.py      download a guests file's models from Sketchfab (needs your API token; plain Python)
    build_preview.py     rebuild the preview pages only (after a colour/effect/race change)
    export_assets.py     pack the renders into the add-on: hounds/<id>.webp, crowd/<id>.webp, props/<id>.webp,
                         guests/<id>.webp, derby_hounds.json, derby_crowd.json, derby_props.json, derby_guests.json,
                         derby_lines.json, derby_credits.json; --local packs a local guest pack instead
    make_ground.py       the track's dirt: a seamless ground texture (source/ground/) made into derby_ground.webp
                         (grey, blotches evened out: the overlay lays it over each theme's colours; plain Python + Pillow)
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

## The track

The ground is "Mud Cracked Dry 03" (Poly Haven, CC0: <https://polyhaven.com/a/mud_cracked_dry_03>, its 1k diffuse
JPG in `source/ground/`), made into a 512 px grey tile (no Blender):

```
python -I art/derby/scripts/make_ground.py art/derby/source/ground/mud_cracked_dry_03_diff_1k.jpg <abs path>/catalog/games_derby/static/derby_ground.webp
```

The overlay bakes it once per theme (an overlay blend over the theme's track colours, each band of the track at its
own scale and depth) and then only slides each band along as the camera moves: blending it every frame stalled the
canvas when drawn in software (a frame in ten took 30 ms). `GROUND` in derby.js: how much world one tile spans, the
camera's focal length (how steeply we look down), the band height, how strongly it shows. Other CC0 grounds tried:
red_dirt_mud_01 (a fine red racing dirt), brown_mud_dry (trampled dirt): swap the source and run it again.

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

A guest racer is a rigged model in `source/guests/<model>/` (a .glb, .gltf, .fbx or .blend), listed in a guests file:
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
`joints` in the spec fills in the rest (a left joint's right one is its name mirrored: `Left`/`Right`, `_L`/`_R`, `.L`/`.R`, `left`/`right`
or Biped's `Bip001 L Thigh`; a rig named any other way, `L_Knee_jnt` or `leg_upper_lf`, names its right joints too). A file with
several characters in it (Among Us crewmates in every colour) keeps one: `keep`, a pattern for its materials (the
others' skeletons go, with their meshes). The run is made, like the crowd's moves: 16 frames of one stride, the feet
on a running path (two-bone IK, knees forward), the arms pumping against the legs (bent from the upper arm), a lean
and a bounce (`run`: lean in the spine, hip (the pelvis tipping in too), stride, stance, lift, kick, bounce, drop,
arm, elbow, arm_lean (how much of the lean the arms follow), out (how wide the arms swing: 0.12, D'oh-mer's 0.5
or his near arm sinks into his belly), nod, still (joints left as they stand, following their parent: Pyramid
Scheme's knife arm)). A sprinter leans: about 35 degrees all told
(lean + hip), or it looks like it's out for a stroll. A centaur (`gait: centaur`, `legs`, `hips`, `head`, `torso`,
`arms`) gallops instead: four legs on a rotary gallop's footfalls, swinging from the shoulder and hip, the front
knees and rear hocks folding in the swing, the body pitching, the rider half leaning in, its arms pumping (`gallop`:
torso, lean for the head, arm). A long cloak hides the legs, so let the wind take it: its skirt bones go in `trail`
like a tail. A whole horse (`gait: horse`, Neigh Sayer) gallops from key poses instead (`guestlib.HORSE`, after
Muybridge): a transverse gallop (far hind, near hind, far fore, near fore, then all four folded under it in the air),
each leg four bones from the shoulder or hip to the pastern (`legs`; no `_end` bones: the hoof is found in its mesh,
and so is the ground), every segment set to an angle from straight down, so a rest pose with a leg raised doesn't
matter. The body rides as high as its planted hooves hold it up and pitches a little, each planted hoof is held on
the ground (two-bone IK on its top two joints), and the stride is as far as a planted hoof slides back. Its neck
(`neck`: bones) stretches out low and nods, lowest on the forelegs, and its head pushes its nose out (`gallop`: neck,
nod, head degrees; pitch, hop, lead, stance, front, rear). The rear keys bend the stifle forward and the hock
back: the centaur gallop has those two the wrong way round, under Centorrhea's cloak it never showed.
A tail bone whose only child is an exporter's `_end` marker pointing away from the bone's own mesh streams toward
the middle of that mesh instead: the marker's place is a guess (Neigh Sayer's pointed forward, and the wind bent her
tail tip up in a hook; Mint's ribbon markers are fine and stay).
Loose parts stream behind (`trail`: {bone-name pattern: [share, lift, wave]}): each bone of a tail, a ponytail or a
ribbon turns `share` of the way toward the wind (behind, `lift` degrees above level) and the chain waves twice a
stride. Without it, `guestlib.TRAIL` takes a game rip's swing bones: `Sp_Hi_Tail*` (the tail), `Sp_He_Hair1-9*`
(the hair, not the bangs) and the jacket tails and ribbons. A model with no swing bones (most) keeps them still,
and a part stuck rigidly to the head can't stream at all. A part that must keep its shape (a wing: streamed bone
by bone its membrane crumples) is turned whole instead (`bend`: {bone-name pattern: [about up, about its left]},
degrees from how it rests; + about up swings a left part back, so its right one takes the minus, and + about its
left sweeps what hangs below back): a dragon's wings, spread at rest, fold back at `[90, 65]` / `[-90, 65]` on
their first wing bone, and what hangs from it follows. A model that came half
skinned (a static body over a skinned one, every face expression as its own mesh) is fixed in the spec: `hide`
parts, `attach` parts rigidly to a bone (attach things to what they hang on: a sword on a centaur's flank goes on
its horse spine, not its rider), `skin` parts with automatic weights from the listed bones only (`upper`: above a
height, and with `y_max` only in front of it, only those bones, so a centaur's hands don't ride its knees; without
`y_max` the cut took its rump too, and leaning the rider levered the rump up out of the cloak), and `squeeze` a body
under a cloak sideways (`by`: a share, fading in over `z` and `y` so the rider isn't touched): seen only from the
side nothing shows, and the bulges of a gallop (body and cloak are skinned differently) stay under the cloak.
(Pushing it in along its normals folds it and pokes out somewhere else.) To find what pokes through, render each
motion alone (zero the rest of `gallop`), and hide one part at a time.
A model that came with no skeleton at all (a static mesh: Pyramid Scheme) gets one: `rig`, `{joint: [x, y, z]}`
where each joint sits in the model as it came (world units; Hips, Spine..Spine2, Neck, Head, the left leg and arm,
plus `HeadTop`, `LeftHandEnd`, `LeftToeEnd` for where those bones end; a right joint not given is the left one
mirrored across x = 0). Each bone runs to the next joint down its chain, and the meshes are skinned with automatic
weights (a vertex the heat weighting misses goes to its nearest bone). Parts named in `attach` stay unskinned and
ride their bone (the helmet on the head), and `hold` (`{bone: [[x, y, z], [x, y, z]]}`) puts every loose piece
whose middle is in that box on that bone alone (the knife in his right hand, which shares a mesh with his arms).
To place the joints, slice the mesh by height (where the legs part, the elbows, the knees) and check a front and a
side render. It then runs like any rigged guest; he walks (`stance` over 0.5: both feet down a while).
A robot of hard parts wants `rigid: true` instead of automatic weights: each loose piece rides the bone nearest
its middle alone (Optimus Grime's 93 plates). Put the feet joints near the soles (a made run plants the feet
joints where they stand) and the knees high, so a long leg plate rides the shin rather than leave a gap when the knee bends. A file
whose parts its own animation moves about, with no skeleton (Optimus Grime's: a robot standing beside a copy that
turns into the truck), stands as at one frame of it (`frame`), and `crop` (`[[x, y, z], [x, y, z]]`, world, as it
came) keeps only the parts whose middle is in that box: at frame 44 the truck has driven out of the robot's box.

A Blender file (a `.blend`, as Blend Swap hands them out) brings its whole scene: what its first scene renders comes
in, with whatever moves it (its skeleton, a hook's empty, even where the file hides them), and its cameras, lights
and control shapes stay out (`guestlib.open_blend`; still no `-Y`, so its scripts never run). It stands at its rest
pose with every constraint gone: a toy whose pieces hang on each other by constraints keeps them where they held it
(Brick Johnson), and its IK goes too, so a control rig whose hands and feet hang on IK controls rather than on the arm
and leg needs `parents` to hang them back on the chain (Kermit the Fraud's chest, head, hands and feet; Botty
White's hands and feet). Its skeleton is the one that skins it (a face rig whose bones only slide a texture about
doesn't count: Brick gets a made `rig`, `rigid`, with `hold` putting each whole leg on its thigh and each arm with
its hand on the upper arm). Its ground and backdrop are meshes it renders: `hide` them (`hide` takes curves too:
Kermit's puppet rods). An old file's texture may hang on a Blender Internal texture slot, which Blender no longer
reads: `paint` (`{material: image in its folder}`) puts it back on by its UVs (Spider-Manspread). `hue`
(`{material name start: degrees}`) turns every colour a material sets round the colour wheel: Kermit's felt is a
yellow-lime that came out mustard under the hounds' warm lights, +30 makes him green.

That same file's animation is the robot turning into the truck, so Optimus Grime does it in the race: `morph`
makes a guest change shape partway (the overlay picks the moment in its `at` share of the track, `[0.3, 0.6]`, per
race and the same on every overlay; it plays the change once over `dur` seconds, says one of its own `morph`
lines, always heard, and runs on in its new shape). Whatever on the track slows it (junk, slime, the hand) changes
it again, back (the change played backward) or into the new shape, never while it's still changing; partway only
turns it into the new shape, if it isn't that already. Render the guest first, then `render_morph.py`: it takes the
parts the file's animation moves over `frames` (`[0, 30]`: the copy, flipping into the truck; the robot standing by
is hidden; a later frame first plays it backward: Sideswipe Right's file turns its Dodge into the robot, so
`[266, 80]` folds the robot back into the Dodge; keep `n` at 16, the overlay counts a guest's footfalls on its first
sheet's 16 frames), turns them bit by bit from facing the race's way to `heading` (the way the new shape faces at the end,
`[x, y]` as it came: measured from its wheels, front axle minus rear) so the truck ends side on, sets them down
from the first shape's ground to the last's, and pins their front at `front` (px across a `w` x `h` frame), easing
from where the first shape's front is to its nose, so it doesn't wander off as it flips. Then the new shape:
`then` (`w`, `h`, `front`, `stride`, `bob`: px it bounces), as the change leaves it. A file can leave it tipped
(Optimus's truck ended 3 degrees nose down and rolled 10 toward us, the back wheels in the air): `then.level` stands
it flat on its wheels, levelling it bit by bit as it changes. The wheels are found, not named: the parts low down that
are round from the side, each with the roundest big one (its tyre; a mudguard can be nearly as big, not as round)
whose middle and axle it shares (rim, hub), and the ground is the plane under their middles. `then.roll` turns them,
that many times a cycle, and the stride becomes as far as that rolls it. Pick it for the rim: a whole number of turns
(the loop closes), and a step a frame that moves the rim's pattern forward less than half way to the next hole, or
it strobes backward (Optimus's ten holes: 2 turns = 45 degrees a frame, a quarter of the way on; 3 would step
back). Wheels skinned to a skeleton instead (Sideswipe's Dodge: one mesh, a bone a wheel) are found as a hover's
are (`hover.roll` below) and turn with them; his five spokes take 1 (22.5 degrees a frame; 2 would step back). `grow` makes the new shape
bigger than the file has it, growing as it changes (2: Optimus's truck, twice the size; give the change's frame
the room: it flings parts high mid-flip). Both come out at the first
shape's px a metre (render_guest.py writes `ppm` into its anchors) as `out/guests/<id>__morph` and `<id>__then`,
and the export packs them next to its sheet (`morph`: `{at, dur, into, then}`, each a sheet and anchors):

```
blender -b -P art/derby/scripts/render_morph.py -- <abs path>/art/derby/source/guests/local.json <abs path>/art/derby/source/guests <abs path>/art/derby/out/guests [ids]
```

A model that comes with a run of its own plays it instead (`anim`: `{"action": its name, "frames": [from, to]}`,
one stride: both feet once; check the action's length, some hold two). It keeps its rest as it came, up is the
world's, and a run that carries it along (root motion) is held in place: the stride is as far as the run moved it,
or else as far back as a planted foot slides. `joints` then only needs the hips, head and legs, and anything that
moves on two "feet" will do (Totally A Barrel's are its hands). Its lowest point over the stride is the ground. A
walk works too, but the overlay steps the frames by the distance run, so a walk's short stride plays far too fast
at race speed (Totally A Barrel's 63 px: 11 strides a second, a flicker): give it a longer `stride` (px; 170 plays
about 4 a second) and it skates a little instead. A run whose feet never plant (a flier, a sprint on stubby legs:
the stride comes out near 0) needs a `stride` too. Two import traps: a rig whose moves
come out torn into spikes (or whose rest stands in pieces) wants `import: {"guess_original_bind_pose": false}`
(the importer's options). It's the first thing to try on a game rip from Sketchfab: a head floating off in front
of the neck, a skeleton lying on its back, a body skinned 100 times too big or flying off out of frame were all
this; a flat rig (every deform bone on the root, moved by controls the export dropped) needs
its chain back before it can be given a run: `parents` (`{bone: parent}`, the bone may be a pattern:
`"DEF-ARM\\.(L|R)": "DEF-SHOULDER.\\1"`), including any control bone something hangs on (Lord Horny's jaw and
claws). `lights` turns the hounds' lights up for a dark model (`{"key": 2, "fill": 3}`: key, rim, lava, fill), and
`x` is where the hips sit across the frame (0.5; a long tail behind wants more). `matte` turns off metal: a rip
whose packed map reads as metallic renders its cloth black (there's no world to reflect). `dither` draws its blended
materials dithered: EEVEE doesn't sort a blended mesh's faces against each other, so a body set to blend (the glTF
importer does it for any texture with alpha, even an opaque one) showed its own far side through in dark blotches
(Neigh Sayer came out a leopard). `turn` (degrees about
up) turns the side view for a clip that runs another way than the rest faces (we'd see its back: Jackass's
`turn: 90`; Honk Hogan's 62, else he's seen 3/4 from behind and crab walks: measure the head's way in the clip
against the rig's forward). `mirror` (a guest with its own run only) views it from its other side and mirrors
the frames, left and right swapping: a skater stands side-on to his board, so from the board's right we'd see
Buzzkill Lightyear's back; mirrored we see his chest and he pushes with his right foot. The face anchor (`mouth`, which
also gives the `nose` the finish line goes by) is the frontmost point of the mesh the head moves: a beak's or a
snout's tip.

Two-legged things that aren't people: `run.knee: -1` bends the knees backward (a walker like an AT-ST, a bird),
and `arms: false` lets a rig with no arms run (nothing to pump). Anything that floats, rolls or drives instead
(`gait: hover`: a podracer, a train, a car, a flying egg) needs no skeleton at all: it bobs and pitches about its
middle (`hover`: bob, a share of its height; pitch, degrees, nose up as it rises; waves, bobs a stride; lift, how
high above the ground it floats, a share of its height) and its feet never touch down (no footfalls). It has no
joints to tell which way it faces: `forward` is the axis it faces as it came (`-y`, the glTF way, unless told
`+x`, `-x` or `+y`). A rigged one can still play a clip of its own while it bobs (`anim`: a train's rods, a car's
wheels; `frames` [to, from] plays it backward, for a part that spins the wrong way), and `stride` is how far one bob carries it (px; twice its length if not given).
A rigged car whose clip leaves its wheels still (McQueef's driving idle only wobbles them) gets `hover.roll`: its
wheels turn that many times a bob, and the stride becomes as far as that rolls it. The wheels are found, not named:
the bones whose own mesh is a disc on edge with its bottom at the ground (McQueef's tyre bones; the mudguards that
ride the axle aren't round), turned about its left through their middles, the clip's own keys on them muted (a
render plays the clip again, over the turn). Pick it for the rim as for a morph's `then.roll` (McQueef: 2, the
LIGHTYEAR lettering and the ten rim holes both step forward). Its `height` is how tall
it is in the frame (px). A file whose parts were placed by object animation comes in pieces (the import keeps
whichever clip it ran first): `move` puts them back (`{object: [x, y, z]}`, world units as it came; Pennydumb's
body and balloon). It works for a rigged guest too, and comes first: a made `rig`'s joints are where the parts
stand after it (Pennydumb's 29 clips only move whole parts: he got a made rig, the balloon `attach`ed to his
near hand and that arm `still`).

A guest someone painted instead of modelled (a PNG of run frames in a grid on a plain background, facing right:
Lil' Chuppy) skips Blender: `sprite` (`{file: the PNG in its folder, cols, rows}`, the grid found from the gaps
between the frames when left out) and `import_sprite.py` writes the same frames and anchors render_guest.py would.
The background (the colour round each frame's edge, and any pocket of it the body closes in) goes, the edge keeps a
soft matte with the grey taken back out of it (no halo on the dark track). Painted frames never sit quite where the
last one did, so each is moved to put its body (the top of it: legs swing) on one spot and its row's lowest paw on
the ground line. The anchors are measured from the pixels: the nose (frontmost up high), the mouth and head just
behind it, the tail (backmost up high), the paws (the lowest points of the bottom band, down within 4 px of the
ground). It needs `height` (px it stands, spikes and ears included) and `stride` (a painted cycle has no feet to
measure it by: set it so the paws don't skate). Frames painted out of order twitch from pose to pose instead of
running: `sprite.order` runs them in another order (their indices, left to right, top to bottom). Find it by
trying every loop and keeping the one whose silhouettes change least from frame to frame, then run it the way that
lands after the leap (Lil' Chuppy's `[0, 3, 5, 7, 1, 2, 4, 6]`: in the air, landing, gathering, crouched, leaping;
31% less change a frame than as painted):

```
python -I art/derby/scripts/import_sprite.py <abs path>/art/derby/source/guests/local.json <abs path>/art/derby/source/guests <abs path>/art/derby/out/guests [ids]
```

A guest with `"mute": true` (a pet) says nothing at all: no lines of its own and none of the hounds'. Give it `at`
(what the others say to it) and `ticker` (the commentary) only; when it passes someone, they mostly answer it.

Before writing a guest's spec, look inside the model: `probe_guest.py` lists every bone (with its parent), the
joint family it matches and the joints it can't find, and every clip (name, frames; a run's name is flagged).
`fetch_guests.py` downloads the models of a guests file from Sketchfab (each guest's `credit.url`), skipping any
folder that already has one. Sketchfab only lets an account download, so it needs your API token (sketchfab.com,
Settings, Password & API) in `~/.sketchfab_token` or `SKETCHFAB_TOKEN` (or download each model's glb by hand,
signed in, into its folder). Read the clip lengths before trusting them: a 40-frame walk can hold two steps of a
200-frame clip, and an "in place" run's feet barely slide. Render, then look at the sheet: a guest's mistakes are
obvious there and nowhere else:

```
python art/derby/scripts/fetch_guests.py <abs path>/art/derby/source/guests/local.json <abs path>/art/derby/source/guests [ids]
blender -b -P art/derby/scripts/probe_guest.py -- <abs path>/art/derby/source/guests <abs path>/art/derby/out/probe [folders]
```

The frames are `w` x `h` px with the guest `height` px tall standing (from its feet joints up: a size set before
the soles went on the ground keeps it), lit like the hounds and seen the way they are
(running to the right). The anchors give the overlay what a hound's do: the ground, the stride (how far one cycle
carries it, so the feet don't skate), the nose (the finish line goes by it), the head and mouth (its name tag, the
photo finish), the hips and backside (a flaming arse) and the feet (down or up). The export adds `sole`, where the
feet land (px from the frame's top: the median lowest solid row of the frames with a foot down, measured from the
pixels; the shadow goes there, since `ground` is the soles' level standing or the lowest point over the stride, a
wingtip or one dipping toe, and can be 20 px off), and `hover` for a floater (its shadow stays on the ground). The hounds get
`sole` too. The export packs the add-on's into
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
