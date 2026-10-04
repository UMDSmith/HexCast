# Avatars

Live2D avatars — and PNGtubers — that your AI drives through Hexcast's API — a lighter VTube Studio without face tracking. **The bot is the tracker**: it says what to do (speak this line, look at chat, be happy, nod, raise an arm), and Hexcast keeps the model alive in between — blinking, breathing, swaying, eyes that wander, the head moving with the voice.

- [Set up](#set-up)
- [The Avatars tab](#the-avatars-tab)
- [Models and items](#models-and-items)
- [What keeps a model alive](#what-keeps-a-model-alive)
- [Mouth and lipsync](#mouth-and-lipsync)
- [Face, emotions, expressions, motions](#face-emotions-expressions-motions)
- [PNGtubers](#pngtubers)
- [Items](#items)
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

One source shows every avatar. For one avatar per source (to put them in different scenes, or under different filters) add `?avatar=main` — or `?avatar=main,guest` for some of them. `?audio=0` keeps a source silent when another one already plays the speech.

## The Avatars tab

- **The live preview** is the same renderer as OBS, so what you see is what is on stream. **Drag** an avatar to move it, **scroll** to zoom around the cursor, drag the **dot** above it to rotate, **double-click** to reset; hold **Shift** to snap. Items drag and scroll the same way. The bar above it shows the OBS source's frame rate and what each avatar costs.
- **Avatars** are named slots — `main`, `guest`, `cat` … — each showing a model. The name is what your bot uses. There is no limit on how many; the same model can be on two avatars. Chips above the preview select one; **Bring forward** / **Send back** change which is in front.
- **The inspector** (right) for the selected avatar: Placement · Face & Mood (emotion, expression, motion and gesture buttons, a look-at pad, the emotion table) · Mouth (lipsync source, meters, tuning, "learn this voice") · Idle · Parameters (every parameter of the model, live; drag one to take it over) · Items · Light · API (ready-made calls for this avatar).
- **Lock** an avatar so it can't be dragged by accident; untick **Visible** to take it off stream.
- **Activity** at the bottom lists what bots asked for and what happened on stream (speech started / ended, motion finished, errors). A bot that streams `params` (or `release`) many times a second gets one line per avatar every few seconds — `x104 (26/s): MyHeadX, MyMouthOpen` — instead of one per message.

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
- **An audio device** — a microphone, a virtual cable, or a loopback of what a playback device plays (Windows). Hexcast records it (16 kHz mono) and streams it to the overlays, which run the same analysis. For a bot that plays its voice somewhere itself. Needs `numpy` and `soundcard` (installed with the plugin).
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

An avatar can show a **PNGtuber** instead of a Live2D model — everything else (the name the bot uses, placement, speech and lipsync, emotions, gestures, items, light, the OBS source) works the same.

**Simple** — a few pictures per state: **quiet**, **talking**, **blinking** and **talking + blinking** (any can be left out), and if you like a **half-open** mouth for quiet talking and a **mouth per vowel** (A / I / U / E / O — the same vowel detection Live2D models use). States are sets of those pictures (`neutral`, `happy` …).

- **Library → New PNGtuber from pictures…** (or import a folder / zip of them). File names place the pictures by themselves: `idle` / `quiet` / `closed mouth` → quiet, `talk` / `open mouth` / `loud` → talking, `blink` / `eyes closed` → blinking, both → talking + blinking, a word in front makes a state (`happy_talk.png`). Anything that can't be placed is filled in order and fixed on the **PNGtuber** tab.
- **The PNGtuber tab** picks the picture for each role and state, adds states and pictures, and tunes it: **Bounce** (how high it hops when it starts talking), **Gravity**, **Talks above** (how loud the voice has to be — see the meter on the Mouth tab), **Hold** (keeps the mouth open between words) and **Breathe**. Changes show in the preview at once; **Save** puts them on stream.

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
| `GET /avatar/api/status` | OBS overlays connected, avatars, frame rate and cost per avatar |
| `GET /avatar/api/avatars` | every avatar's settings and its live state (held parameters, expressions, emotion, gaze …) |
| `GET /avatar/api/avatars/<name>/info` | the model's parameters (`id`, `name`, `group`, `min`, `max`, `default`), parts, art meshes, hit areas, expressions, motions, emotions, gestures, tracker inputs (`inputs`: VTube Studio's, then the model's custom ones, also listed alone as `custom_inputs`), face controls, mappings |
| `GET /avatar/api/avatars/<name>/params/live` | every parameter's value right now (from the OBS overlay) |
| `GET /avatar/api/models` · `GET /avatar/api/items` | the library |

Parameters and art meshes appear once the model has been drawn (by an overlay or the tab) — Hexcast learns them from the model itself.

### Avatars and the library

| Call | Body |
| --- | --- |
| `POST /avatar/api/avatars` | `{"name": "guest", "model": "akari"}` — create |
| `POST /avatar/api/avatars/<name>` | any settings: `model`, `visible`, `locked`, `x`, `y`, `scale`, `rotation`, `flip`, `idle {…}`, `mouth {…}`, `emotions {…}`, `light {…}` |
| `POST /avatar/api/avatars/<name>/rename` | `{"to": "host"}` |
| `POST /avatar/api/avatars/<name>/delete` | |
| `POST /avatar/api/order` | `{"names": ["back", "middle", "front"]}` — draw order |
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
| `config/avatar.json` | every avatar's settings (placement, idle, mouth, emotions, items, light) and the stage settings |
| `config/avatar_runtime/` | the downloaded Live2D runtime and when its license was accepted |
| `media/avatars/models/<id>/` (PNGtuber) | its pictures and `pngtuber.json` (the rig: states, roles or layers, bounce ...) |
| `media/avatars/models/<id>/` | a model as shipped, plus `hexcast.json` (Hexcast's settings for it: mappings, idle animation, physics) and `hexcast.info.json` (its parameters, cached) |
| `media/avatars/items/<id>/` | an item |

## Live2D licensing

Live2D's Cubism Core is Live2D Inc.'s software under the [Live2D Proprietary Software License Agreement](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html), and the renderer embeds Live2D's Cubism Framework ([Live2D Open Software License](https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html)). Hexcast doesn't ship either: each streamer reads and accepts Live2D's terms in the Avatars tab, and the files are downloaded to that PC from Live2D and npm. Live2D asks publishers of apps that load users' own models ("expandable applications") for a [publication license](https://www.live2d.com/en/sdk/license/expandable/). Your models' own terms (from their artist and rigger) apply as well.

## Troubleshooting

- **The overlay stays empty** — the Live2D runtime isn't set up (open the tab), the avatar is hidden, or its model can't be drawn (the tab says why). `?note=1` on the overlay URL shows problems on the overlay itself.
- **No sound from speech** — tick **Control audio via OBS** on the browser source (or OBS plays nothing from it), and check `overlays` in the `speak` answer.
- **The mouth moves too little / all the time** — raise **Gain** / raise **Cutoff** on the Mouth tab; watch the meters.
- **"saved by Cubism Editor 5.3"** — export the model again for Cubism 5.0 – 5.2.
- **A model shows two versions of a part at once** (four arms ...) — Hexcast picks the parts its animations use; if the wrong set shows, switch it with **Parameters → Parts** or the `parts` call.
- **An expression or motion is missing** — it must be a `.exp3.json` / `.motion3.json` inside the model's folder; press **⟳** next to the model (or `POST …/reload`) after adding files.
- **The device list is empty** — numpy / soundcard are missing: press **Repair** on the Avatars card in **+**. On Linux a loopback needs PulseAudio or PipeWire.
