# Avatars

Live2D avatars — and PNGtubers — that your AI drives through Hexcast's API — a lighter VTube Studio without face tracking. **The bot is the tracker**: it says what to do (speak this line, look at chat, be happy, nod, raise an arm), and Hexcast keeps the model alive in between — blinking, breathing, swaying, eyes that wander, the head moving with the voice.

- [Set up](#set-up)
- [The Avatars tab](#the-avatars-tab)
- [Overlays: one OBS source per avatar, or several together](#overlays-one-obs-source-per-avatar-or-several-together)
- [Models and items](#models-and-items)
- [What keeps a model alive](#what-keeps-a-model-alive)
- [Mouth and lipsync](#mouth-and-lipsync)
- [Face, emotions, expressions, motions](#face-emotions-expressions-motions)
- [PNGtubers](#pngtubers)
- [Items](#items)
- [Colors](#colors)
- [Light](#light)
- [API](#api)
- [Performance](#performance)
- [Files](#files)
- [Live2D licensing](#live2d-licensing)
- [Troubleshooting](#troubleshooting)

## Set up

1. **Install** Avatars from the **+** tab.
2. **Set up Live2D** (once). The tab asks you to read Live2D's license agreements; tick the box and press **Download and set up**. Hexcast fetches Live2D's Cubism Core and the renderer (about 1.3 MB) into `config/avatar_runtime/`. Offline: upload `live2dcubismcore.min.js` from [Live2D's download page](https://www.live2d.com/en/sdk/download/web/) instead.
3. **Add a model** to the library (a zip, a folder, or straight from VTube Studio if it is on this PC) and press **+ Avatar**.
4. **OBS**: add a Browser source with `http://localhost:4747/avatar/overlay`, 1920×1080, and tick **Control audio via OBS** — speech the bot sends plays through this source.

That source shows the avatars on the **main overlay** — all of them, until you make another overlay. To size and place avatars in OBS one by one, give each its own overlay ([below](#overlays-one-obs-source-per-avatar-or-several-together)). `?audio=0` keeps a source silent when another one already plays the speech.

## The Avatars tab

- **The live preview** is the same renderer as OBS, so what you see is what is on stream. **Drag** an avatar to move it, **scroll** to zoom around the cursor, drag the **dot** above it to rotate, **double-click** to reset; hold **Shift** to snap. Items drag and scroll the same way. The bar above it shows the OBS source's frame rate and what each avatar costs.
- **Avatars** are named slots — `main`, `guest`, `cat` … — each showing a model. The name is what your bot uses. There is no limit on how many; the same model can be on two avatars. Chips above the preview select one (the ones on the overlay you are looking at); **Bring forward** / **Send back** change which is in front of the others on its overlay.
- **Overlays** are the row above the chips: one tab per OBS source, a green dot when that source is connected, **+ Overlay** to add one. The preview shows the selected overlay only.
- **The inspector** (right) for the selected avatar: Placement · Face & Mood (emotion, expression, motion and gesture buttons, a look-at pad, the emotion table) · Mouth (lipsync source, meters, tuning, "learn this voice") · Idle · Parameters (every parameter of the model, live; drag one to take it over) · Items · Light · API (ready-made calls for this avatar).
- **Lock** an avatar so it can't be dragged by accident; untick **Visible** to take it off stream.
- **Activity** at the bottom lists what bots asked for and what happened on stream (speech started / ended, motion finished, errors). A bot that streams `params` (or `release`) many times a second gets one line per avatar every few seconds — `x104 (26/s): MyHeadX, MyMouthOpen` — instead of one per message.

## Overlays: one OBS source per avatar, or several together

An **overlay** is one OBS Browser source: a 1920×1080 stage with the avatars you put on it. Every avatar is on exactly one overlay (the **main** overlay unless you say otherwise).

Why more than one: OBS scales, crops and moves a *source* as a whole. Two avatars on one overlay always move and scale together, and cropping the source to fit one cuts off the other. Give an avatar its own overlay and it is its own source — size it, move it, crop it, put it in another scene or under a filter, and nothing else on stream changes.

- **+ Overlay** (above the preview) makes one and asks for a name — `guest`, say. Its OBS source URL is `http://localhost:4747/avatar/overlay/guest`; main's stays `http://localhost:4747/avatar/overlay`. The URL under the preview is always the overlay you are looking at.
- **+ Avatar** adds the new avatar to the overlay you are looking at. To move an existing one: select it and use **Overlay** at the top of its **Placement** tab (or `POST /avatar/api/avatars/<name>` with `{"overlay": "guest"}`). It leaves one source and appears on the other at once, in the state it is in (held parameters, expression, emotion …); its position (X, Y, zoom) is kept, so check it in the preview.
- **Position and zoom are inside the overlay.** Dragging an avatar in the preview places it on its own overlay's stage; where that stage sits on the stream is OBS's business, and the tab does not show how overlays stack against each other.
- Each source only plays the speech of its own avatars, and only the commands for them reach it. An avatar a bot addresses by name works the same wherever it is — the API never mentions overlays.
- **Delete overlay** (not for main) removes it; its avatars are not deleted but move to main. Remove the OBS source too — the URL then draws nothing.
- At most 12 overlays. A name is 1–32 lowercase letters, digits, `_` or `-`.
- `?avatar=a,b` on any overlay URL narrows that source to some of *its* avatars (an avatar on another overlay is not drawn there). Sources made this way before overlays existed keep working: everything was on main, and `?avatar=` still picks from it — move the avatar to its own overlay instead if you want to scale it on its own.
- A typo in the URL (`/overlay/gust`) draws nothing and shows no error on stream; add `?note=1` to see what is wrong. `GET /avatar/api/overlays` lists the overlays, what is on them and how many OBS sources are connected to each.

## Models and items

**Models** are Live2D models made with Cubism 3, 4 or 5 — a folder with a `.model3.json`, a `.moc3` and textures. They come in three ways:

- **Import a zip** — the folder holding the `.model3.json` is kept (a whole export zip is fine).
- **Import a folder** — pick the model's folder in the browser.
- **From VTube Studio** — if VTube Studio is installed (Steam) the library lists its models; tick and import. Its `.vtube.json` is read: the parameter setup (the rigger's input ranges and smoothing), the expressions and animations its hotkeys use, its idle animation and the physics switch. Expression and motion files that are only in the folder (VTube Studio lists them in hotkeys, not in the model) are found too.

Models saved by **Cubism Editor 5.3** (moc3 version 6) can't be drawn by the web renderer yet; the library says so — export again in the 5.0 – 5.2 format. Cubism 2 models (`.model.json` + `.moc`) are not supported.

**Items** (props) are a `.png`, `.jpg`, `.webp` or animated `.gif`, a folder of numbered frames (`name_1.png`, `name_2.png` …, played at a frame rate you choose), or a Live2D item (a model folder). VTube Studio's own Items folder can be imported in one go.

Everything is kept in `media/avatars/models/` and `media/avatars/items/` — copy a folder in by hand and it shows up after a reload of the tab.

## What keeps a model alive

Without a camera something has to move the face. Each avatar runs a **virtual tracker** that produces VTube Studio's tracking inputs (`FaceAngleX/Y/Z`, `EyeOpenLeft/Right`, `EyeRightX/Y`, `Brows`, `MouthSmile`, `MouthOpen`, `VoiceA` … `VoiceO` and the rest):

| | |
| --- | --- |
| Blink | every few seconds, sometimes twice |
| Breath | `ParamBreath` (or whatever the model maps breath to) |
| Sway | slow drift of the head and body — **Sway** 0 – 100 |
| Eyes wander | short glances around the camera, now and then further away — **Eyes wander** 0 – 100 |
| Talking motion | small nods and head movement on the emphasis of the voice — **Talking motion** 0 – 100 |
| Idle animation | the model's own idle animation (its `Idle` motion group, or the one its VTube Studio setup names), if you leave it on |
| Physics | the model's hair and cloth physics |

The inputs go through the model's **mappings** — its VTube Studio parameter setup, or VTube Studio's defaults for a model without one — so a model looks the way its rigger set it up. The tracker works **relative to each model's neutral pose** (worked out from the parameters' defaults), so the same "smile" or "eyes 80 % open" looks right on any rig. Mappings can be edited in **Parameters → Mappings**.

Idle motion is seeded by the avatar's name and the clock, so the preview and OBS move in step. Everything here is only the default: the API overrides any of it at any moment.

An idle animation keeps the body; the face (mouth, eye opening, gaze) stays with the tracker so lipsync and blinking still work. A motion you trigger owns every parameter it animates — except the mouth while the avatar is talking.

## Mouth and lipsync

Pick, per avatar, where the voice comes from (**Mouth → Voice from**):

- **Speech sent to Hexcast** — the bot hands over the audio (`speak`, below) and the OBS overlay plays it. The lipsync reads the very samples being played, so it is never out of step. `Lead` delays what you hear a few tens of milliseconds so the mouth is never late.
- **An audio device** — a microphone, a virtual cable, or a loopback of what a playback device plays (Windows). Hexcast records it (16 kHz mono) and streams it to the overlays, which run the same analysis. For a bot that plays its voice somewhere itself. Needs `numpy`, `soundcard` and `sounddevice` (installed with the plugin; `sounddevice` records the microphones `soundcard` can't open). The line under the device says if it is recording, through what, and how loud the input is.
- **Nothing** — the bot moves the mouth itself with `params` / `face` (any parameter, any rate — see the API).

The analysis (advanced lipsync) finds the voice's formants every frame and turns them into VTube Studio's inputs: `VoiceVolume`, `VoiceSilence`, `VoiceFrequency` and the vowels `VoiceA` / `VoiceI` / `VoiceU` / `VoiceE` / `VoiceO` (they add up to at most 1). A model with vowel shapes mapped to them (VTube Studio's advanced-lipsync setup) uses them; every model also gets `MouthOpen` and a mouth form (wide for I/E, round for U/O). Formants differ from voice to voice: by default the analysis tunes itself to the speaker while it talks; **Learn from a file** tunes it to a recording at once (30 s or more of the voice — your TTS reading anything). Gain, cutoff, mouth size and smoothing are on the Mouth tab, with live meters.

A bot's own mouth values win over the lipsync for as long as it holds them (with their weight), so mixing both works.

## Face, emotions, expressions, motions

- **Face** controls work on any model, relative to its neutral pose: `smile` (-1 frown … 1 smile), `brows` (-1 … 1), `eyes` (0 shut … 1 normal … 1.6 wide), `mouth_open`, `cheek` (blush / puff, if the model maps it), `mouth_x`, `tilt`, `wink_left`, `wink_right`.
- **Emotions** are named face shapes plus, optionally, any number of the model's own expressions and a motion: `neutral`, `happy`, `sad`, `angry`, `surprised`, `smug`, `sleepy`, `thinking`, `embarrassed`, and any you add. When an avatar is made, expressions whose names suggest an emotion (`Smile`, `Mad`, `Shock`, `Cry` …) are filled in; change the table on **Face & Mood → Emotion → expression table**. `intensity` scales the face, `for` reverts after so many seconds.
- **Expressions** are the model's `.exp3.json` files. Several can be on at once (each with its own fade), the way VTube Studio stacks them — and one call can switch several (`"names": ["Smile", "Blush"]`; `"only": true` switches every other one off in the same step). Names match in any case. In the tab, tick **Pick several**, click the expressions and press **Fire together** (or **Only these**).
- **Motions** are the model's `.motion3.json` animations, by name (or group + index); `loop` repeats one.
- **Gestures** are built-in head movements for any model: `nod`, `double_nod`, `shake`, `tilt`, `bounce`, `lean_left`, `lean_right`, `look_away`.
- **Parts** are whole pieces of the model (a second set of arms, glasses, a prop): `parts` shows or hides them (opacity 0 – 1), `release_parts` gives them back to the model. Many models switch parts in their animations (two arm poses, for instance) without a Live2D pose file; Hexcast applies those switches the way VTube Studio does, and at rest shows the parts as the idle animation (or the first animation that sets them) begins — so such a model never shows every variant at once.
- **Look**: a direction (`x`, `y` in -1 … 1, from the viewer: +x right, +y up), a named spot (`camera`, `left`, `up_right` …), or a point on the stage (`stage_x`, `stage_y` in %, e.g. where the chat box is) — the eyes go there and the head follows (`head` 0 … 2). `release` lets the eyes wander again.

## PNGtubers

An avatar can show a **PNGtuber** instead of a Live2D model — everything else (the name the bot uses, placement, speech and lipsync, emotions, gestures, items, colors, light, the OBS source) works the same.

**Simple** — a few pictures per state: **quiet**, **talking**, **blinking** and **talking + blinking** (any can be left out), and if you like a **half-open** mouth for quiet talking and a **mouth per vowel** (A / I / U / E / O — the same vowel detection Live2D models use). States are sets of those pictures (`neutral`, `happy` …).

- **Library → New PNGtuber from pictures…** (or import a folder / zip of them). File names place the pictures by themselves: `idle` / `quiet` / `closed mouth` → quiet, `talk` / `open mouth` / `loud` → talking, `blink` / `eyes closed` → blinking, both → talking + blinking, a word in front makes a state (`happy_talk.png`). Anything that can't be placed is filled in order and fixed on the **PNGtuber** tab.
- **The PNGtuber tab** has three parts. Changes show in the preview at once (the preview card stays in view while you scroll the settings); **Save** puts them on stream, **Undo** goes back to what is saved.
  - **Faces** — each state is a small grid, eyes across (open / shut) and mouth down (closed / open), of big picture slots. Click a slot to pick from this PNGtuber's pictures (or **Upload** one), or **drop a picture file straight on it**; × takes it away. Only the top-left one (mouth closed, eyes open) is needed; the mouth-open ones make it talk and the eyes-shut ones make it blink. *More mouths* adds a half-open mouth (for quiet talking) and one per vowel A / I / U / E / O. **Add pictures…** puts more pictures in the PNGtuber's folder; **Make it layered** turns it into layers.
  - **Voice** — a live meter of the voice with a white line where the mouth opens, and whether it is open right now. **Mouth** is *Follows each word* (the default: the mouth opens as the voice rises and shuts in the dips between words and sounds, so it opens and closes with the speech, and blinking carries on as usual) or *Opens and stays open while talking* (the old behaviour: open at any sound and for **Stays open (s)** after it). **Opens above** is how loud the voice has to be; **Word detail** is how deep a dip has to be to shut the mouth. **▶ Test voice** plays a made-up voice (words of vowel-shaped syllables with pauses between) through the preview, so you can tune all of this without an audio file; it is silent unless **Hear speech** under the preview is ticked, and it never reaches OBS.
  - **Movement** — **Hop at the start** (how high it jumps when it starts talking), **Bounce with the voice** (a small hop on each word as the audio comes in, bigger for a louder voice; 0 = none), **Fall speed** and **Breathe**. The **Idle** tab's *Talking motion* scales both hops.

  The mouth follows the voice for layered PNGtubers too (a layer that shows *only talking* is the open mouth), and for a bot that drives `MouthOpen` itself.

**Layered** — pieces stacked and attached to each other, like **PNGTuber Plus**: import its `.save` file as it is (**Import a zip / .save…**). Each layer has a picture, a parent, a position and pivot offset, a depth, when it shows (always / only quiet / only talking / only talking softly, and always / eyes open / blinking), the states (costumes) it belongs to, wobble, follow-lag, swing, squash & stretch, sprite-sheet frames, and clipping of its attached layers. **Make it layered** turns a simple PNGtuber into layers to build on.

What the API does with a PNGtuber:

| | |
| --- | --- |
| `speak`, a device, `MouthOpen` (`params`), `face` `mouth_open` | talking (the mouth pictures) |
| blinking (built in), `EyeOpenLeft` / `EyeOpenRight`, `face` `eyes`, `wink_*` | blinking |
| `expression` / `emotion` | switch the **state** — one at a time (an emotion uses the state of the same name, or the one its table names) |
| `gesture`, `look`, idle sway | move, bob and tilt the whole picture (`bounce` hops) |
| `parts` | show / hide **layers** by id |
| `motion` | not for PNGtubers (an error says so) |

Items pin to a PNGtuber's layers (they follow its bounce and wobble).

## Items

On the **Items** tab add items from the library to the avatar. Their position is in % of the model's box (0,0 its top left), their size 1 = a quarter of the model's height, and they sit **in front of** or **behind** the model. They move, scale and turn with the avatar.

**Pin** an item and it is glued to the part of the model under it — a hat to the head, a drink to a hand — following that art mesh as it moves, tilts and turns (the item keeps its angle relative to it). Drag a pinned item in the preview and it re-pins wherever you drop it; drop it off the model and it is unpinned. Items take the avatar's light.

## Colors

The **Colors** tab does what VTube Studio's *customize multiply / screen colour for art meshes* does — and presets and hotkeys, which here are API calls your bot makes.

Pick what to colour in the list: the **whole model**, a **part** (a folder of art meshes and sub-parts — hair, eyes, clothes) or a single **art mesh**. A part says how many art meshes are inside it ("Horns · 8 art meshes"); **click it and they open under it**, each one colourable on its own — just like picking its meshes one by one in VTube Studio. **Show → All art meshes** is the flat list of every art mesh of the model, each with the part it is in; **Open all / Close all** do the same for the tree. The **filter** matches ids and names, and an art mesh also answers to its part's name, so *horn* finds every mesh of the horns.

Art mesh names are only what the model's artist gave them (`ArtMesh11`), so what you select **flashes on the preview** — a part, a single mesh, even one that is hidden (it shows through for the flash); **Where?** flashes it again. Or press **Pick on the model** and click the thing you want in the preview: its row opens and lights up. The flash is the preview only; it never reaches OBS. Then set, for the selected one:

- **Multiply** — a colour the art is multiplied by. It tints and darkens (white = no change) and keeps the shading, outlines and highlights.
- **Overlay** — a colour added on top, VTube Studio's *screen* colour. It lightens (black = no change), so a dark part can be made light, which multiply alone can't.
- **Alpha** — 100 % to 0 %: fades the thing out. **Hide** is alpha 0 — a prop, a hat, a second set of arms.

They stack: a mesh's multiply is its own × its part's (and the parts around that) × the whole model's; overlays combine as screens; alphas multiply. They go over a mesh's own multiply / screen colour and opacity, so a model that animates its own colours still does. Every change fades. **×** takes a field off, **Clear this** takes everything off the selected one, **Reset all** takes everything off the avatar. A PNGtuber's layers are its parts (a layer's parent only moves it, it doesn't group it) and have multiply and alpha — there is no overlay colour for them.

What is set here belongs to the **avatar**.

### Presets

**Save the colours below as a preset…** records what is set on the avatar as a named preset of its **model**: kept in the model's folder (`hexcast.colors.json`), so it goes wherever the model goes and every avatar showing that model can use it. Click a preset's name to switch it on or off; **✎** makes its colours the avatar's own (to change them, then save again under the same name), **×** deletes it.

Presets are VTube Studio's *ArtMesh Color Preset* hotkeys, and the bot switches them through the API (`color_preset`): **on**, **off** or **toggle**, any number at once (each over the ones before it, field by field), `"only": true` to switch every other off, `for` to switch one off by itself, `fade` to blend. A preset that is on is drawn over the avatar's own colours; what a bot holds with `colors` goes over both. A preset that sets a colour back (white, alpha 1) can undo what the avatar's own colours do.

### The model's default, and loading a preset

A preset is also a **saved config of the model**, to bring back whenever you load it again:

- **Load** on a preset makes its colours the avatar's own for good (it asks first only if that would replace different colours — save those as a preset first to keep them). That is the quick way to put a config on an avatar.
- **☆ / ★** makes a preset the model's **default**: an avatar that loads the model starts in it — a new avatar with that model, or an avatar you switch to it (an avatar always starts bare in a model with no default; colours are of one model's parts, so they don't carry over to another). Colours given when an avatar is created (API `colors`) win over the default. **Save the colours below as a preset…** has a *Default* box for it; the first preset of a model is the default unless you untick it.
- Avatars that are already set up are not touched when you change the default; press **Load** on them.
- **As model** saves a preset **together with its model as a new model**: a full copy of the model's files in the library (so it takes the model's size again), named as you like, that starts in the preset's colours — and the original is left alone, so it still loads without them (if that preset was the original's default, it stops being it). It is an ordinary model: the colours are kept as settings, not painted into the pictures, so you can change them, add presets, or delete either one without touching the other. The dialog can switch the avatar over to the new model at once.

### Layers

From bottom to top: the **avatar's own** colours (the tab), the **presets switched on** (the order they went on), then what a bot **holds** with `colors`. A later one wins, field by field — a preset that sets only an overlay leaves the multiply below it alone. The tab says when a bot is holding colours, with a **Let go** button; a colour you change in the tab wins over one a bot holds for the same thing.

## Light

Per avatar: a coloured **key light** from a direction (0 = from the right, -90 = above, 180 = the left), a **rim light** on the edges facing it, and an **ambient tint**. **Presets** animate it: pulse, flicker, fire (flickering orange from below), police (red / blue), rainbow, strobe, lightning, neon. The API can change it for the moment (`light`) or for good (`"save": true`); `fade` blends.

## API

Every call takes the avatar's name; `*` addresses every avatar at once. Bodies are JSON; numbers can be sent as numbers or strings. Times (`for`, `duration`, `fade`) are seconds. Errors answer `{"ok": false, "error": "..."}` with a 4xx status. `GET /avatar/api` lists everything.

### Parameters — full control

```
POST /avatar/api/avatars/<name>/params
{"values": {"ParamMouthOpenY": 0.8, "ParamAngleX": 15, "MouthSmile": 1},
 "mode": "set",        set | add (offset from what it would be)
 "weight": 1,          0..1 blend over the model's own value
 "duration": 0.3,      ease to the value over this long
 "ease": "smooth",     linear | in | out | inout | smooth | back | bounce
 "for": 2,             let go after 2 s (leave out: held until released)
 "hold": true,         false = a pulse that lets go right after `duration`
 "fade": 0.3,          how long letting go takes
 "layer": "input"}     input (before expressions and physics - hair follows a turned head)
                       | final (over everything, physics included)
POST /avatar/api/avatars/<name>/release   {"ids": ["ParamMouthOpenY"], "fade": 0.3}   ({} = all)
```

A key is a **Live2D parameter id** (`GET .../info` lists the model's, with ranges and display names) or a **tracker input** — VTube Studio's input names (`FaceAngleX`, `MouthOpen`, `MouthSmile`, `EyeOpenLeft`, `VoiceA` …, in VTube Studio's ranges), which then go through the model's mappings like the built-in tracker's.

A model's own mappings can name **custom inputs** too — VTube Studio's *custom parameters* (`MyHeadX`, `MyMouthOpen` … whatever the model's `.vtube.json` maps; `GET .../info` lists them as `custom_inputs`, and in `inputs` after the standard ones). Send them like any other input: the row's input range, output range and smoothing apply. Nothing in the tracker moves a custom input, so hold the value and keep it updated; until you send one it rests where the output parameter's default puts it. A name that is both a mapping's input and a Live2D parameter id is the **input** — the mapping wins, as in VTube Studio. A row marked *use blinking* (a custom eye input, say) blinks — toward the low end of its output range — on top of the value you hold, whenever **Blink by itself** is on in the avatar's Idle settings.

For values every frame (your own lipsync, head motion from a tracker of your own) use the WebSocket with `"hold": false` — or hold and keep updating.

### Everything else

| Call | Body |
| --- | --- |
| `POST …/expression` | `{"name": "Smile", "state": "on"\|"off"\|"toggle", "for": 5, "fade": 0.4}`; several at once: `{"names": ["Smile", "Blush"]}`, plus `"only": true` to switch every other one off. The answer lists `states` (what each became) and `active` (all that are on). An unknown name is a 400 listing the model's expressions |
| `POST …/parts` | `{"values": {"PartArmA": 0, "PartArmB": 1}, "fade": 0.3, "for": 5}` — part opacity 0 – 1, held until released |
| `POST …/release_parts` | `{"ids": ["PartArmA"]}` (`{}` = all) |
| `POST …/colors` | `{"parts": {"PartHair": {"multiply": "#ff8800", "overlay": "#202040", "alpha": 0.9}}, "meshes": {"ArtMesh12": {"alpha": 0}}, "all": {"multiply": "#aabbcc"}, "fade": 0.3, "for": 10, "save": false}` — recolour parts (`…/info` lists `parts`, `art_meshes` and which part each art mesh is in), art meshes and the whole model (`all`). A spec has any of `multiply` (darkens / tints), `overlay` (lightens) — `#rrggbb`, `#rgb` or `rrggbb` — and `alpha` (0 invisible … 1); a bare colour (`"PartHair": "#ff8800"`) is a multiply, `"hidden": true` is `alpha: 0`. Held until released or `for` runs out; `null` for a field (or a whole target) lets it go back; `"save": true` keeps it in the avatar's settings instead (merged field by field). A PNGtuber's layers are its `parts` (no `overlay`, no `meshes`) |
| `POST …/release_colors` | `{"parts": ["PartHair"], "meshes": ["ArtMesh12"], "all": true, "fade": 0.3}` — what a bot holds goes; `{}` = everything it holds |
| `POST …/color_preset` | `{"name": "Night", "state": "on"\|"off"\|"toggle", "for": 20, "fade": 0.4}`; several at once: `{"names": ["Night", "Neon"]}`, plus `"only": true` to switch every other off. Any case. A preset saved on the avatar's model (`color_presets` in `…/info`); an unknown name is a 400 listing them. The answer lists `states` and `active` |
| `POST …/clear_color_presets` | `{"fade": 0.4}` — every preset off |
| `POST …/clear_expressions` | `{"fade": 0.4}` |
| `POST …/motion` | `{"name": "wave"}` or `{"group": "TapBody", "index": 0}`, `"loop": true`, `"priority": "force"` |
| `POST …/stop_motion` | |
| `POST …/emotion` | `{"name": "happy", "intensity": 1, "for": 6, "fade": 0.5}` |
| `POST …/face` | `{"smile": 0.6, "brows": 0.3, "eyes": 0.9, "for": 3}` or `{"release": true}` |
| `POST …/look` | `{"x": 0.7, "y": 0.2}`, `{"at": "camera"}`, `{"stage_x": 85, "stage_y": 30}`, `"head": 1`, `"for": 4`, `"speed": 1`, or `{"release": true}` |
| `POST …/gesture` | `{"name": "nod", "amount": 1, "duration": 0.7}` |
| `POST …/transform` | `{"x": 70, "y": 55, "scale": 1.2, "rotation": 0, "flip": false, "duration": 1, "ease": "out"}`; `by_x` / `by_y` / `by_scale` / `by_rotation` move relative; `"save": true` keeps it; `{"reset": true}` goes back to the saved placement |
| `POST …/show` · `POST …/hide` | `{"fade": 0.4, "save": false}` |
| `POST …/speak` | the audio: a multipart file field `audio`, the raw bytes as the body, or JSON `{"url": "https://…"}` / `{"audio_b64": "…"}`; options `volume` (0–2), `text` (for the log), `interrupt` (stop what is playing; otherwise lines queue), `wait=1` (answer when the line has finished) |
| `POST …/stop_speaking` | |
| `POST …/item_add` | `{"item": "halo", "id": "halo1", "x": 50, "y": 5, "scale": 1, "rotation": 0, "layer": "front"\|"back", "opacity": 1, "flip": false, "fps": 12}` |
| `POST …/item_update` | `{"id": "halo1", …any of the above…, "visible": false, "pin": null}` |
| `POST …/item_remove` · `…/items_clear` | `{"id": "halo1"}` |
| `POST …/light` | `{"enabled": true, "preset": "fire", "color": "#ffd9a8", "intensity": 0.8, "angle": -35, "rim": "#7fb6ff", "rim_amount": 0.6, "ambient": "#ffffff", "ambient_amount": 0.2, "speed": 1, "fade": 0.6, "save": false}` or `{"reset": true}` |
| `POST …/reload` | load the model again |
| `POST …/command` | `{"cmd": "<any of the above>", …}` |

`speak` answers `{"ok": true, "id": "…", "overlays": 1}` — `overlays` is how many OBS sources play it (0 = nobody heard it; the answer says so). With `wait=1`: `"finished": true, "duration": 3.42` once it has played, or `"result": "speech_error"` if the overlay went away.

### Reading

| Call | Answer |
| --- | --- |
| `GET /avatar/api/status` | OBS sources connected (`overlays`, and `by_overlay`: how many for each overlay), avatars, frame rate and cost per avatar |
| `GET /avatar/api/overlays` | each overlay: `id`, the OBS `path`, its `avatars`, the number of OBS `sources` connected |
| `GET /avatar/api/avatars` | every avatar's settings and its live state (held parameters, expressions, emotion, gaze …) |
| `GET /avatar/api/avatars/<name>/info` | the model's parameters (`id`, `name`, `group`, `min`, `max`, `default`), parts, art meshes, hit areas, expressions, motions, emotions, gestures, tracker inputs (`inputs`: VTube Studio's, then the model's custom ones, also listed alone as `custom_inputs`), face controls, mappings |
| `GET /avatar/api/avatars/<name>/params/live` | every parameter's value right now (from the OBS overlay) |
| `GET /avatar/api/models` · `GET /avatar/api/items` | the library |

Parameters and art meshes appear once the model has been drawn (by an overlay or the tab) — Hexcast learns them from the model itself.

### Avatars and the library

| Call | Body |
| --- | --- |
| `POST /avatar/api/avatars` | `{"name": "guest", "model": "akari", "overlay": "guest"}` — create (`overlay` is optional: main; an overlay that doesn't exist is a 404) |
| `POST /avatar/api/avatars/<name>` | any settings: `model`, `overlay`, `visible`, `locked`, `x`, `y`, `scale`, `rotation`, `flip`, `idle {…}`, `mouth {…}`, `emotions {…}`, `colors {…}` (`{"all", "parts", "meshes"}` as above; replaces the saved set), `light {…}` |
| `POST /avatar/api/avatars/<name>/rename` | `{"to": "host"}` |
| `POST /avatar/api/avatars/<name>/delete` | |
| `POST /avatar/api/overlays` | `{"name": "guest"}` — a new overlay, an OBS source at `/avatar/overlay/guest` |
| `POST /avatar/api/overlays/<name>/delete` | its avatars move to main; main itself can't be deleted |
| `GET /avatar/api/models/<id>/color_presets` | the model's colour presets: `{"presets": {"Night": {"parts": {…}, "meshes": {…}, "all": {…}}}}` |
| `POST /avatar/api/models/<id>/color_presets` | `{"name": "Night", "avatar": "main", "default": false}` records what that avatar's colours are now as a preset (or `{"name": "Night", "look": {…}}`); the same name in any case replaces it; `"default": true` makes it the model's default. `POST …/color_presets/<name>/default` (`{"default": false}` for none) sets or clears the default - what an avatar starts in when it loads the model (`color_default` in the model's entry and in `…/info`); `POST …/color_presets/<name>/as_model` (`{"name": "Hiyori Night"}`) saves that preset together with the model as a new model of the library - a full copy that starts in it; the answer is its model entry; `POST …/color_presets/<name>/delete` removes one |
| `POST /avatar/api/order` | `{"names": ["back", "middle", "front"]}` — draw order (among the avatars of one overlay, the later is in front) |
| `POST /avatar/api/models/upload` | multipart `file` (zip) or `files` + `paths` (a folder) |
| `POST /avatar/api/models/import` | `{"path": "C:/…/my model"}` — a folder on the Hexcast PC |
| `POST /avatar/api/models/<id>/settings` | `{"name": …, "mappings": […], "physics": true, "idle_motion": "…"}` or `{"reset_mappings": true}` |
| `POST /avatar/api/items/upload` · `/items/import` | like models (`{"paths": [...]}` imports several) |

Uploads, imports and deletes only accept requests from Hexcast's own pages or from scripts (no `Origin`), like the plugin store.

### WebSocket

`ws://localhost:4747/avatar/ws/control` — for bots that send a lot, or want to know what happened:

```json
→ {"cmd": "params", "avatar": "main", "values": {"FaceAngleX": 12, "MouthOpen": 0.4}, "hold": false, "rid": 7}
← {"ok": true, "avatar": "main", ..., "rid": 7}
→ {"cmd": "speak", "avatar": "main", "url": "http://127.0.0.1:5005/line.wav"}
← {"type": "event", "event": "speech_start", "avatar": "main", "id": "…", "duration": 3.4}
← {"type": "event", "event": "speech_end", "avatar": "main", "id": "…", "duration": 3.4}
```

Any REST command works as `{"cmd": …, "avatar": …, …}` (plus `list` and `info`). Replies only come when you send a `rid` (or when something failed). Events: `speech_start`, `speech_end`, `speech_error`, `motion_start`, `motion_end`, `loaded`, `error`.

## Performance

- One WebGL canvas draws every avatar; an avatar costs what its model costs, nothing more.
- A hidden avatar (or one faded out) isn't updated at all.
- **Idle → Update rate** runs an avatar at 30 or 20 fps — for one that hardly moves; **Stage → Frame rate cap** caps the whole OBS source.
- No camera, no tracking, no extra program: the work is the model's own deformation and drawing, done on the GPU of the PC running OBS.
- The bar above the preview shows the OBS source's frame rate and the time each avatar takes per frame.

## Files

| | |
| --- | --- |
| `config/avatar.json` | every avatar's settings (its overlay, placement, idle, mouth, emotions, items, colors, light), the list of overlays and the stage settings |
| `config/avatar_runtime/` | the downloaded Live2D runtime and when its license was accepted |
| `media/avatars/models/<id>/` (PNGtuber) | its pictures and `pngtuber.json` (the rig: states, roles or layers, bounce ...) |
| `media/avatars/models/<id>/` | a model as shipped, plus `hexcast.json` (Hexcast's settings for it: mappings, idle animation, physics), `hexcast.info.json` (its parameters, cached) and `hexcast.colors.json` (its colour presets and which is the default - a PNGtuber has one too) |
| `media/avatars/items/<id>/` | an item |

## Live2D licensing

Live2D's Cubism Core is Live2D Inc.'s software under the [Live2D Proprietary Software License Agreement](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html), and the renderer embeds Live2D's Cubism Framework ([Live2D Open Software License](https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html)). Hexcast doesn't ship either: each streamer reads and accepts Live2D's terms in the Avatars tab, and the files are downloaded to that PC from Live2D and npm. Live2D asks publishers of apps that load users' own models ("expandable applications") for a [publication license](https://www.live2d.com/en/sdk/license/expandable/). Your models' own terms (from their artist and rigger) apply as well.

## Troubleshooting

- **The overlay stays empty** — the Live2D runtime isn't set up (open the tab), the avatar is hidden, or its model can't be drawn (the tab says why). Or the avatar is on another overlay: each source shows only its own (the tab says which overlay you are looking at, and its URL). `?note=1` on the overlay URL shows problems on the overlay itself.
- **Two avatars scale and move together in OBS** — they are on the same overlay, which is one source. Make an overlay for one of them (**+ Overlay**, then **Overlay** on its Placement tab) and add that URL as its own Browser source.
- **No sound from speech** — tick **Control audio via OBS** on the browser source (or OBS plays nothing from it), and check `overlays` in the `speak` answer.
- **The mouth moves too little / all the time** — raise **Gain** / raise **Cutoff** on the Mouth tab; watch the meters.
- **"saved by Cubism Editor 5.3"** — export the model again for Cubism 5.0 – 5.2.
- **A model shows two versions of a part at once** (four arms ...) — Hexcast picks the parts its animations use; if the wrong set shows, switch it with **Parameters → Parts** or the `parts` call.
- **An expression or motion is missing** — it must be a `.exp3.json` / `.motion3.json` inside the model's folder; press **⟳** next to the model (or `POST …/reload`) after adding files.
- **The device list is empty** — numpy / soundcard are missing: press **Repair** on the Avatars card in **+**. On Linux a loopback needs PulseAudio or PipeWire.
- **My microphone does nothing, but Voicemeeter / a virtual cable works** — look at the line under **Device** on the Mouth tab: it says whether Hexcast is recording the device and how loud the input is (or why it can't). Hexcast records with `soundcard` first; a plain (often mono USB) microphone can report a Windows audio format `soundcard` doesn't understand, and then Hexcast records it through PortAudio (`sounddevice`) instead — the line says "through PortAudio". If that package is missing, press **Repair** on the Avatars card in **+** (it installs `sounddevice`). "almost nothing is coming in" means the device is muted, turned down in Windows' Sound settings, or not the microphone you are talking into (Windows' *Let desktop apps access your microphone* privacy switch can also silence it).
