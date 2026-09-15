# Countdown — customisable countdown timer overlay

A single countdown timer you position on a scaled 1920×1080 stage exactly like
the chat window — count down a fixed duration, or count down to a wall-clock
time. The server owns the authoritative remaining time and streams it to the
overlay, which anchors to it and ticks locally, so clock skew between machines
never matters. Resolution is to the second.

Everything lives under `/countdown/*`.

---

## Two ways to run it

- **Duration** — count down a fixed span (H:M:S) from the moment you press
  Start.
- **Target** — count down to a wall-clock time, server-local (e.g. `22:00`).
  If that time has already passed today it rolls to tomorrow.

**Autostart** begins the countdown the moment an overlay connects — useful for
a "Starting soon" scene that should just run when OBS loads it.

---

## Install

Copy `countdown.py` next to `hexcast.py`, and these two files into `static/`:

```
static/countdown_panel.html
static/countdown_overlay.html
```

`hexcast.py` already mounts the module when the file is present. If you're
wiring it into your own copy by hand, it's the same two lines as the other
integrations, after `app.mount("/media", ...)`:

```python
from countdown import attach_countdown
attach_countdown(app, PORT)
```

---

## Browser source

| Source | URL |
| --- | --- |
| Countdown | `http://localhost:4747/countdown/overlay` |

Add it as a 1920×1080 Browser source — it scales to whatever size you give it,
so everything keeps its proportions. Uncheck **Shutdown source when not
visible**.

---

## Styling & placement

All from the panel at `/countdown`:

- **Text & format** — optional caption above/below the digits, text shown at
  zero, hours shown always/never/auto, font family/size/weight/spacing,
  colors, outline, drop shadow.
- **Background** — the same styles as the chat overlay: solid, gradient,
  glass blur, border frame, glow, image (cover), or a 9-slice image frame.
  The image library is shared with the Twitch chat/alert overlays, so a
  background uploaded in any of them shows up in all three.
- **Placement** — drag boxes on a 1920×1080 stage: a blue box for the
  digits/caption, a red box (or full-screen) for the background panel.

Settings persist in `config/countdown.json`.

---

## Media cues

Auto-fire soundboard clips against the master timer — add **as many as you
like** (or none) with the **+ Add clip** button, each anchored to its own
countdown threshold. Every cue names a library (audio or video), a clip, an
anchor, and a number of seconds remaining:

- **End at … s left** — the clip *finishes* at that threshold. On **Start**
  (or autostart) the server reads the clip's playable length — honouring any
  start/end trim saved in the clip's editor — and counts backwards to trigger
  the start at the right moment. `0` seconds left = the clip ends exactly at
  0:00 (e.g. intro music finishing as the timer hits zero).
- **Start at … s left** — the clip *starts* the instant the countdown reaches
  that threshold. No length maths — it just fires when the master timer hits
  the given number of seconds remaining.

The **On** switch on each row arms or disarms that cue without deleting it;
the **✕** removes the row. All cues share the one master timer.

Details worth knowing:

- Cues fire through the soundboard's own play path, so each clip's saved
  position, scale, volume, trim and cooldown all apply. Clips play on the
  **soundboard overlay** (`/overlay`), not the countdown overlay — both
  browser sources need to be in the scene.
- Cues are read when you press Start; adding, removing or editing them
  mid-countdown takes effect on the next Start.
- Pause cancels the pending cues; Resume re-arms them against the new end
  time (a clip that already fired won't fire twice in the same run).
- For an **End at** cue whose clip is longer than the time remaining, it
  fires immediately as a best effort.

---

## HTTP API

| Endpoint | What it does |
| --- | --- |
| `GET /countdown/api/status` | connected overlay count + timer snapshot |
| `GET /countdown/api/config` | full config as JSON |
| `POST /countdown/api/config` | merge-and-save any subset of config keys |
| `POST /countdown/api/timer` | `{"action": "start" \| "pause" \| "resume" \| "reset"}` — start also accepts `"seconds"` for an ad-hoc duration that ignores the configured mode |

Examples:

```
curl -X POST http://localhost:4747/countdown/api/timer -H "Content-Type: application/json" -d "{\"action\":\"start\"}"
curl -X POST http://localhost:4747/countdown/api/timer -H "Content-Type: application/json" -d "{\"action\":\"start\",\"seconds\":90}"
curl -X POST http://localhost:4747/countdown/api/config -H "Content-Type: application/json" -d "{\"label\":\"Starting soon\"}"
```

Media cues are settable over `POST /countdown/api/config` like everything
else, as a `media_cues` array. Each entry is an object:

```json
{
  "media_cues": [
    {"enabled": true, "kind": "audio", "name": "intro", "anchor": "end",   "offset": 0},
    {"enabled": true, "kind": "video", "name": "sting",  "anchor": "start", "offset": 30}
  ]
}
```

`anchor` is `"end"` (clip finishes at `offset` seconds remaining) or
`"start"` (clip fires when the countdown reaches `offset` seconds remaining).
Posting `media_cues` replaces the whole list. The legacy single-cue keys
(`media_enabled` / `media_kind` / `media_name` / `media_end_offset`) are still
read once and migrated into a one-element `media_cues` list automatically.

---

## Storage

Everything persists in `config/countdown.json`. Timer state itself is
in-memory and resets on restart. No tokens, no secrets — same security
posture as the rest of Hexcast: no auth, keep it on the LAN.
