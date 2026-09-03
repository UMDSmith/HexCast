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

## Media cue

Auto-fire a soundboard clip so it **ends** at a chosen point in the countdown
— e.g. intro music that finishes exactly as the timer hits 0:00.

Enable it on the panel: pick the library (audio or video), the clip, and how
many seconds should remain on the countdown when the clip ends (`0` = at
zero). On **Start** (or autostart) the server reads the clip's playable
length — honouring any start/end trim saved in the clip's editor — and works
backwards to trigger the start at the right moment.

Details worth knowing:

- The cue fires through the soundboard's own play path, so the clip's saved
  position, scale, volume, trim and cooldown all apply. The clip plays on the
  **soundboard overlay** (`/overlay`), not the countdown overlay — both
  browser sources need to be in the scene.
- The cue is read when you press Start; changing it mid-countdown takes
  effect on the next Start.
- Pause cancels the pending cue; Resume re-arms it against the new end time
  (a clip that already fired won't fire twice in the same run).
- If the clip is longer than the time remaining, it fires immediately as a
  best effort.

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

Media cue config keys (settable over `POST /countdown/api/config` like
everything else): `media_enabled` (bool), `media_kind` (`audio` | `video`),
`media_name` (clip name), `media_end_offset` (seconds remaining when the clip
should end).

---

## Storage

Everything persists in `config/countdown.json`. Timer state itself is
in-memory and resets on restart. No tokens, no secrets — same security
posture as the rest of Hexcast: no auth, keep it on the LAN.
