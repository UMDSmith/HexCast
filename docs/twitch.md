# Twitch — chat and event overlays

Adds a chat overlay, an alert overlay, and a settings panel to Hexcast. Both
overlays are ordinary OBS browser sources, so you size and place them in OBS
like anything else.

Everything lives under `/twitch/*`, so it cannot collide with the soundboard's
routes. `hexcast.py` needs two lines added; nothing else changes.

---

## Install

Copy `twitch.py` next to `hexcast.py`, and these four files into `static/`:

```
static/twitch_panel.html
static/twitch_chat.html
static/twitch_events.html
static/twitch_boot.js
```

Install the dependencies:

```
pip install -r requirements-twitch.txt
```

Add to `hexcast.py`, after `app.mount("/media", ...)`:

```python
from twitch import attach_twitch
attach_twitch(app, PORT)
```

Restart. You'll see three new URLs printed at startup.

### Connecting at boot instead of on first request

Hexcast builds its app with `FastAPI(lifespan=lifespan)`, which makes Starlette
ignore `add_event_handler("startup")`. The Twitch connection therefore opens
lazily, on the first panel or overlay request — fine in practice, but it means
nothing connects until OBS or a browser asks for it.

To connect at startup, edit the existing `lifespan`:

```python
async def lifespan(app: FastAPI):
    ...
    obs.start()

    from twitch import start_twitch, stop_twitch      # add
    await start_twitch()                              # add

    print(f"\n  ==== Hexcast ====")
    ...
    yield
    await stop_twitch()                               # add
    obs.stop()
```

---

## First run

Open <http://localhost:4747/twitch>.

### Chat works with no setup at all

Type your channel name on the Connection tab and save. Chat starts flowing
immediately over anonymous Twitch IRC — display names, colours, badges, Twitch
emotes, and global 7TV/BTTV/FFZ emotes. Add
`http://localhost:4747/twitch/chat` as a browser source and you're live.

### Events need a sign-in

Follows, subs, resubs, gifted subs, bits, raids, channel point redeems, hype
trains and stream online/offline all come from EventSub, which requires OAuth:

1. Create an app at <https://dev.twitch.tv/console/apps/create>.
   Category `Broadcaster Suite`, Client Type `Confidential`.
2. Paste the redirect URL shown in the panel into the app's OAuth Redirect URLs.
3. Paste the Client ID and Client Secret into the panel, save.
4. Click **Connect Twitch account** and approve.

The Status tab lists every EventSub subscription and why any of them failed —
usually a missing scope, which means signing out and back in.

**On redirect URLs.** Twitch allows plain `http` only for the host `localhost`;
a LAN IP would need HTTPS. Use a `localhost` URL to do the pairing. Twitch also
matches the value byte for byte, and treats `localhost` and `127.0.0.1` as
different — the module normalises the loopback IP to `localhost` for you, so
one registered entry covers both.

---

## Browser sources

| Source | URL |
| --- | --- |
| Chat | `http://localhost:4747/twitch/chat` |
| Alerts | `http://localhost:4747/twitch/events` |

For both: set Width/Height to the box size you want, uncheck **Shutdown source
when not visible**, uncheck **Refresh browser when scene becomes active**.

Settings apply live over the websocket — save in the panel and the overlay
restyles itself. After the initial setup you never need to touch OBS again.

Like `control.html` and `overlay.html`, the three Twitch pages are read from
disk on every request, so you can edit the CSS and just refresh.

---

## Chat overlay

Configurable from the panel: font family (pick from the grouped dropdown of
curated Google fonts, previewed in their own face), size, weight, line height,
message and username colours, background style (see below), corner radius,
padding, spacing, text outline and shadow, maximum messages on screen,
auto-fade after N seconds, newest message at top or bottom, alignment, column
width, entrance animation, badge and timestamp toggles, emote size,
first-time-chatter highlighting, and a list of bot accounts to hide.

### Message backgrounds

The **Background style** dropdown (Chat overlay → Colours and box) does more
than a flat colour. The alert overlay has the same set under Alert appearance.

- **Solid** — the classic single colour + opacity.
- **Gradient** — blends the background colour into a second colour at a chosen
  angle.
- **Animated gradient** — the same blend, slowly shifting for a lively look.
- **Glass / frosted** — translucent panel with a frosted blur and a hairline
  edge (the blur amount is the *Glass blur* field).
- **Outlined frame** — a solid box with a coloured border (*Frame width* /
  *Frame / glow colour*).
- **Neon glow** — a coloured outer glow around each message, arcade-style.
- **Image / GIF (fill)** — pick an image or animated GIF from your library. It's
  scaled to cover the whole message (cropping as needed), so it's best for
  photos, patterns and animated GIFs.
- **Image frame (stretch middle)** — a 9-slice frame image. The four corners
  stay crisp at their original size while the edges and centre stretch to fit
  however tall or wide the message grows — the same trick decorative
  achievement/panel graphics use. Pick the frame from your library, then set:
  - **Frame slice (source px)** — how far in from each edge of the *source*
    image the fixed corner ends (i.e. the thickness of the decorative border in
    the file itself).
  - **Frame thickness (px)** — how thick that border renders on screen. Match it
    to the slice for a 1:1 look, or make it smaller if your source art is
    high-resolution.
  - **Frame edges** — *Stretch* (default) smears the edge strips; *Round* /
    *Repeat* tile them instead, which keeps proportions on ornate borders.
  - The centre of the source paints over the background colour, so a transparent
    middle lets your **Background colour / opacity** tint show through. Use a
    static PNG here — animated GIFs don't reliably animate in 9-slice mode.

For both image styles, **Image inner padding** adds space between the text and
the artwork edge, on top of the normal padding — bump it up if a decorative
border is crowding the words.

### Placement — the chat box and its background

Most chat overlays live in a fixed box on the layout. Rather than resizing the
OBS browser source (which scales the source and softens the text), the chat
overlay is **always the full OBS canvas** and you position things *inside* it
from the panel, at full fidelity. Make the browser source your whole screen,
uncheck **Shutdown source when not visible**, and forget about it — all sizing
happens in Hexcast.

The **Placement** card (Chat overlay tab) is a 16:9 preview of your screen with
two draggable, resizable boxes:

- **Chat** (blue) — where messages live. They're clipped to this box, so chat
  stays put instead of spilling across the scene.
- **Background** (red) — the panel the chosen **Background style** paints on. It
  moves and sizes independently of the chat box, so you can, say, sit the text
  in the lower half of a taller decorative frame.

Two switches:

- **Lock chat & background to these boxes** turns placement on. With it off, the
  overlay behaves the classic way (chat flows over the whole source, background
  is per-message).
- **Background fills the whole screen** pins the background panel to the entire
  canvas and greys out its box — for a full-screen backdrop behind a smaller
  chat box.

Drag a box to move it, grab a corner to resize, then **Save chat settings**. In
placement mode the **Background style** (and its image/frame/colour settings)
describes the *panel*; the per-message **Message background** toggle still works
on top if you also want a bubble behind each line.

### Background library

Both image styles pull from a shared library rather than a pasted URL. The
**Image / GIF library** control is a dropdown of everything you've uploaded,
with **Upload** and **Delete** buttons beside it. Uploads (png, jpg, gif, webp,
apng; up to 25 MB) are stored under `media/overlays/` and served from
`/media/overlays/…`, so you can build up a set of chat and alert skins and swap
between them per overlay. Chat and alerts draw from the same library.

Third-party emotes (7TV, BetterTTV, FrankerFaceZ) are fetched per channel and
globally. The channel-specific sets need the numeric Twitch ID, so they only
load once you've signed in; global sets work either way.

---

## Alerts

The Alerts tab is a table of every event type with four things you can set:

- **On** — whether it fires at all
- **Secs** — how long it stays on screen
- **Title / Body** — templates supporting `{user} {amount} {tier} {months} {reward}`
- **Clip** — a Hexcast clip name to fire alongside the alert

The Clip column goes through the same `GET /api/play/{name}` endpoint the bot
API uses, so a raid can trigger an airhorn and a gif in the same beat. It is
fire-and-forget with a short timeout — a missing clip logs a failure and the
alert still shows.

Every alert type has a **Test** button.

### Known gap

There are no per-event cooldowns. A gift bomb of twenty subs produces twenty
`channel.subscription.gift` events and, if you've mapped a clip to it, twenty
clip triggers. Setting a per-clip cooldown in the soundboard's own editor is
the current workaround.

---

## Shoutouts (`!so`)

Type `!so channelname` in your own chat (you or a mod; `@channelname` works
too) and three things happen:

1. The **official Twitch shoutout** banner is sent — the same thing as typing
   `/shoutout`. Twitch only accepts these while you're live.
2. A **chat line** is posted from your account — the template lives in
   `config/twitch.json` under `shoutout.message`, with `{name}`, `{login}`
   and `{url}` placeholders.
3. **Two random clips from their channel** play back to back on the Clips
   overlay (top clips of the last 30 days, falling back to all-time; the
   count is `shoutout.clip_count`, 1–5). They're ephemeral: nothing is
   queued, downloaded, or saved — they just stream through the player and
   vanish. Stop, or manually playing anything, cancels the rest of the
   chain. Requires the Clips module; see [clips.md](clips.md) for the
   overlay setup.

The chat-side parts (1 and 2) need two scopes that were added after this
module first shipped — `moderator:manage:shoutouts` and `user:write:chat`. If
you signed in before they existed, hit **Connect with Twitch** in the panel
once more to grant them; the log says exactly which piece is missing. Without
sign-in at all (anonymous chat mode), the clip still plays — only the chat
messages are skipped.

Config knobs (`config/twitch.json` → `"shoutout"`): `on`, `command` (default
`!so`), `who` (`mods` or `broadcaster`), `native`, `message`, `clip`,
`clip_count`.

Bots can trigger the clip half directly:
`GET /clips/api/shoutout/{channel}?count=2`.

---

## Feeding a bot or a model

Set **Forward URL** on the Connection tab. Every chat message and every alert
is POSTed there as JSON:

```json
{
  "type": "chat",
  "id": "...",
  "ts": 1753600000.0,
  "user": {"login": "viewer", "name": "Viewer", "color": "#00ff00", "badges": []},
  "flags": {"broadcaster": false, "mod": true, "vip": false, "sub": true, "first": false},
  "bits": 0,
  "reply": null,
  "text": "hey there",
  "fragments": [{"t": "text", "v": "hey there"}]
}
```

Alerts arrive as:

```json
{"type": "event", "kind": "raid", "title": "Raid", "body": "SomeStreamer raided with 42",
 "user": "SomeStreamer", "amount": "42", "duration": 9.0}
```

---

## Security

Same posture as the rest of Hexcast: no authentication. This module adds a
Twitch client secret and a user OAuth token in `config/twitch_secrets.json`.
The API never returns either, but anyone who can reach port 4747 can
reconfigure your overlays and read your chat. Keep it on the LAN, and keep
`config/` out of git.

---

## Troubleshooting

**Panel says "chat only (anonymous)" after signing in.** The channel name
didn't resolve, or the token expired. Hit Reconnect and check the Status log.

**A subscription failed with 403.** Missing scope. Sign out and back in so
Twitch re-prompts for the full list.

**A subscription failed with 400 "invalid subscription type and version".**
Twitch has retired that version of the event. The subscription list in
`twitch.py` (`SUB_PLAN`) carries a version string per type; check the current
one at <https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/> and
update it. Hype train moved from v1 to v2 this way.

**Redirect URL mismatch.** The URL registered on Twitch must match byte for
byte, including port and path.

**Overlay blank in OBS.** Right-click the source → Interact → check the
console. Usually "Shutdown source when not visible" killed the websocket.

**Custom font isn't loading.** The name goes straight to Google Fonts, so it
has to match a real family (`Bebas Neue`, not `bebas`). Fonts installed
locally on the machine running OBS also work.

**Third-party channel emotes missing.** They're looked up by numeric Twitch ID
and only load after sign-in. Global sets work regardless.
