# Discord — voice-reactive overlay

Adds a Discord-Reactive-Images-style overlay and a settings panel to Hexcast.
Everyone in your current voice channel appears in an OBS browser source; their
image lights up while they speak and dims when silent. Each person can use
their Discord avatar or an uploaded idle/talking image pair, PNGTuber style.

Everything lives under `/discord/*`, so it cannot collide with existing
routes. No bot and no server-side Discord app: the module talks to the
**Discord desktop client running on the same machine** over its local RPC
WebSocket, the same channel Discord's own StreamKit overlays use.

---

## Install

Copy `discord_reactive.py` next to `hexcast.py`, and these two files into
`static/`:

```
static/discord_panel.html
static/discord_overlay.html
```

Install the dependencies:

```
pip install -r requirements-discord.txt
```

`hexcast.py` already mounts the module when the file is present; nothing else
changes. If you're wiring it into your own copy by hand, it's the same two
lines as the other integrations, after `app.mount("/media", ...)`:

```python
from discord_reactive import attach_discord
attach_discord(app, PORT)
```

Restart. You'll see two new URLs printed at startup.

### Connecting at boot instead of on first request

Hexcast builds its app with `FastAPI(lifespan=lifespan)`, which makes Starlette
ignore `add_event_handler("startup")`. The RPC connection therefore opens
lazily, on the first panel or overlay request. To connect at startup instead,
edit the existing `lifespan`:

```python
async def lifespan(app: FastAPI):
    ...
    obs.start()

    from discord_reactive import start_discord, stop_discord   # add
    await start_discord()                                      # add

    print(f"\n  ==== Hexcast ====")
    ...
    yield
    await stop_discord()                                       # add
    obs.stop()
```

---

## First run

Open <http://localhost:4747/discord> with the **Discord desktop app running**
(the browser version has no RPC socket) and click **Connect**.

### How the approval dialog works

Discord's local RPC requires the user's consent once per application. When
Hexcast connects, Discord itself pops a dialog — *"StreamKit wants to access
your account"* — and nothing proceeds until you click **Authorize** inside
Discord. Hexcast then exchanges the approval code for an access token and
caches it in `config/discord_secrets.json`, so subsequent starts reconnect
silently.

Why "StreamKit"? Discord only honours the `rpc` scope for applications it has
approved, so the module authorizes as Discord's own StreamKit app by default —
exactly what reactive.fugi.tech and StreamKit itself do. Nothing is sent to
any third party; the only external call is the code-for-token exchange with
`streamkit.discord.com`. If you have your own approved application, the
Advanced section of the panel takes a custom client ID and token-exchange URL.

If you decline the dialog, the module parks itself rather than nagging —
click **Connect** in the panel when you're ready to be asked again. The rest
of Hexcast is unaffected either way, including when Discord isn't running at
all (the module just retries quietly in the background).

### After connecting

Join a voice channel. The panel's Participants tab and the overlay both fill
in immediately, and follow you automatically when you switch channels. Leaving
voice empties the overlay.

---

## Browser source

| Source | URL |
| --- | --- |
| Voice overlay | `http://localhost:4747/discord/overlay` |

Add it as a Browser source in OBS: transparent background, so size and place
it like any other element. Uncheck **Shutdown source when not visible**,
uncheck **Refresh browser when scene becomes active**.

Settings apply live over the websocket — save in the panel and the overlay
restyles itself. You can also override any layout setting per scene with
query params, so one saved config can serve several scenes:

```
http://localhost:4747/discord/overlay?layout=column&size=96&show_names=0
http://localhost:4747/discord/overlay?layout=grid&grid_columns=2&spacing=30
```

Booleans take `0/1`. Keys match the panel: `layout` (row/column/grid),
`grid_columns`, `size`, `spacing`, `align`, `show_names`, `name_size`,
`name_color`, `shape` (circle/rounded/square), `dim`, `dim_pairs`,
`desaturate`, `bounce`, `hide_muted`, `mute_badge`.

`size` is the default for everyone; a per-user size on the Participants tab
overrides it for that person. `shape` clips Discord avatars only — custom
uploads always render unclipped.

The **Toggle a test user** button on the Connection tab injects a fake
participant and flips their speaking state on every click, so you can style
and place the source without being in a call.

---

## Custom images (PNGTuber mode)

Each participant row on the Participants tab has two upload slots:

- **idle** — shown while silent
- **talking** — shown while speaking

Click a slot to upload a `.png`, `.gif`, or `.webp`. Files are stored under
`media/discord/{user_id}/` and survive restarts. The fallback chain is:

1. idle + talking uploaded → images swap with speech, PNGTuber style, and the
   silent dim is skipped (the closed mouth *is* the silent state) — flip
   **Darken idle+talking pairs too** on the Overlay tab to dim them anyway
2. only idle uploaded → the idle image brightens while speaking
3. nothing uploaded → the Discord avatar, brightening while speaking

The per-user **Mode** select overrides this: `avatar` ignores uploads for that
person, `custom`/`auto` use them when present. Per-user **Size** (0 = overlay
default) and **Offset X/Y** nudge one person without moving the rest.

Custom images are never circle-cropped or rounded: they render at their
native aspect ratio with transparency intact, letterboxed into the (square)
size slot. A decorative frame, a full-body PNGTuber, or any png with a clear
background shows exactly as authored — only Discord avatars get the shape
setting applied.

Tips for making a pair: keep both images the same dimensions with the face in
the same spot, export with transparency, and let the talking frame be the
open-mouth/lit-up variant. Animated `.gif` talking images work and loop while
speaking.

Avatars are proxied and cached by the server (`/discord/avatar/{id}`), so the
overlay never hits Discord's CDN from OBS and there are no CORS surprises.

---

## Overlay behaviour

- **Speaking**: full brightness plus a subtle pop; **silent**: dimmed
  (~40% by default) and desaturated — both adjustable.
- **Muted / deafened**: dimmed harder, never lights up, and gets a small red
  mic/headphone badge (or hide muted people entirely).
- People joining and leaving the channel animate in and out.
- Channel switches are followed automatically — the module resubscribes to
  the new channel's events on Discord's `VOICE_CHANNEL_SELECT`.

---

## Security

Same posture as the rest of Hexcast: no authentication. This module stores an
RPC access token in `config/discord_secrets.json`. The token is scoped to
`rpc` (local client control) and the API never returns it, but anyone who can
reach port 4747 can see who's in your voice channel and reconfigure the
overlay. Keep it on the LAN, and keep `config/` out of git.

---

## Troubleshooting

**Panel says "discord not running".** The RPC socket only exists in the
desktop app (ports 6463–6472), not in the browser client. Start the app; the
module reconnects on its own within seconds.

**No approval dialog appeared.** Discord shows it in the main window — bring
the app to the front. It times out after two minutes; click **Connect** to
resend. If you previously clicked Deny, the module deliberately waits for a
manual Connect.

**"authorization declined or timed out".** You closed or denied the dialog.
Click **Connect** to be asked again.

**Everyone vanished from the overlay.** You left the voice channel, or
Discord restarted. Both fix themselves: rejoin voice, or wait for the
reconnect (status pill in the panel top bar).

**Speaking never lights up for one person.** If they're server-muted or
suppressed, the overlay treats them as muted by design. Otherwise check they
haven't been given a talking image with the wrong extension — only
`.png/.gif/.webp` are accepted.

**Uploads don't show in OBS.** OBS caches aggressively. The image URLs carry
an mtime cache-buster so a re-upload should repaint on its own; if not,
right-click the source → **Interact** → **Ctrl+Shift+R**.

**Using it on a second PC.** The module must run on the machine where the
Discord desktop app runs — the RPC socket only listens on `127.0.0.1`. OBS on
another machine is fine; point the browser source at the Hexcast machine's IP.
