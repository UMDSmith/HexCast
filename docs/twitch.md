# Twitch — chat and event overlays

Adds a chat overlay, an alert overlay, and a settings panel to Hexcast. Both
overlays are ordinary OBS browser sources, so you size and place them in OBS
like anything else.

Everything lives under `/twitch/*`, so it cannot collide with the soundboard's
routes. It is a plugin: add it from the **+** tab.

---

## Install

Open the **+** tab in the top bar, find **Twitch** and press **Install**. The tab appears straight away, and the
Python packages it needs are installed for you - no restart. From a terminal:
`python hexcast.py plugins install twitch`. (More in [Plugins](plugins.md).)

Three URLs are printed at startup (and shown on the Twitch page).

### Connecting at boot instead of on first request

Hexcast builds its app with `FastAPI(lifespan=lifespan)`, which makes Starlette
ignore `add_event_handler("startup")`. The Twitch connection therefore opens
lazily, on the first panel or overlay request — fine in practice, but it means
nothing connects until OBS or a browser asks for it.

To connect at startup, edit the existing `lifespan` in `hexcast.py`:

```python
async def lifespan(app: FastAPI):
    ...
    obs.start()

    from hexcast_plugins.twitch.twitch import start_twitch, stop_twitch      # add (only while the plugin is installed)
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

Add your channel under **Channels** on the Connection tab and save. Chat starts flowing
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

## Watching several channels

A Hexcast can watch up to five channels at once — your main one and a second
or test channel, say. Add them under **Channels** on the Connection tab (a
login, an optional label like `beta`, and an on/off switch); the first is the
**primary**. Everything below is also in `config/twitch.json`:

```json
"channels": [
  {"login": "mainchannel", "label": "primary", "enabled": true},
  {"login": "testchannel", "label": "beta",    "enabled": true}
],
"overlay_channels": "primary"
```

(`"channel"` is still there and always mirrors the primary's login; an older
config that only has `"channel"` becomes a one-entry list by itself.)

**Who Hexcast acts as.** Sign in once per Twitch account (**Connect a Twitch
account** — to add a second account, be signed in to Twitch as that user in the
browser first; a private window works). A channel uses

1. the account with its **own name**, if you have signed in as it — all of that
   channel's alerts (subs, bits, redeems, hype trains, follows …), chat, and
   shoutouts as the broadcaster;
2. otherwise your **default account** (the primary's own account if it is
   signed in, else the first one you signed in with) — that channel's chat, plus
   whatever that user may see there as a moderator (follows, for one). The Status
   tab lists which subscriptions the channel could not get;
3. with nobody signed in, anonymous chat.

The Channels card says which of these each channel is using. Twitch allows three
EventSub connections per login, and each channel is one: don't put more than
three channels on one login.

**Channels are independent, so the primary keeps working whatever the others do.**
Every channel has a connection of its own:

- A channel that cannot start — no network yet at boot, a login Twitch no longer
  accepts, a mistyped name — is retried with a growing pause (2 s up to 60 s) and
  never delays or stops the others. A problem checking one login is logged and
  does not hold anything up.
- Changing the channel list, a label or an on/off switch, or signing an account in
  or out, reconnects **only the channels it concerns**: switching the beta channel
  off or renaming it never touches the primary's connection, and signing in as the
  beta channel's own account reconnects beta alone. A label change reconnects
  nothing; the new label shows on the next line.
- The **Reconnect** button reconnects everything, on purpose.

**What the overlays show.** Every chat line and alert is tagged with its
channel. A source with no `?channel=` in its URL shows what **Chat and alert
sources show** says — by default the primary only, so adding a second channel
never changes what is on your stream. Choose *every channel* there, or decide
per source:

| URL | Shows |
| --- | --- |
| `…/twitch/chat` | the panel's setting (the primary, or every channel) |
| `…/twitch/chat?channel=testchannel` | that channel only |
| `…/twitch/chat?channel=all` | every channel |

The same goes for `/twitch/events`. The Connection tab lists a ready-made URL
for each channel. Tick **Tag each line with its channel** on the Chat tab
(Text) to label merged lines with the channel's label, and use `{channel}` in an
alert's title or body to name it. A chat clear or a deleted message only
affects the channel it happened in. An alert for a channel that no connected
source shows is not waited for, so it cannot hold up the queue.

**Shoutouts** work per channel: `!so` typed in a channel is made in that
channel, as the account Hexcast uses there (which has to be its broadcaster or
a moderator).

---

## Browser sources

| Source | URL |
| --- | --- |
| Chat | `http://localhost:4747/twitch/chat` |
| Alerts | `http://localhost:4747/twitch/events` |

(With several channels, add `?channel=name` or `?channel=all` —
[see above](#watching-several-channels).)

For both: set Width/Height to the box size you want, uncheck **Shutdown source
when not visible**, uncheck **Refresh browser when scene becomes active**.

Settings apply live over the websocket — save in the panel and the overlay
restyles itself. After the initial setup you never need to touch OBS again.

Like `control.html` and `overlay.html`, the three Twitch pages are read from
disk on every request, so you can edit the CSS and just refresh.

---

## The Chat and Alerts tabs

Both tabs are laid out the same way:

- **Start from a look** — one click fills in the text and background settings
  with a ready-made style (Clean dark, Frosted glass, Neon arcade, Retro
  terminal, Storybook, Candy pop, Midnight, Bare text). It only changes the form
  and the preview; adjust whatever you like afterwards.
- **A row of sub-tabs** groups the settings — *Text · Background · Layout ·
  Motion* and, on Chat, *Highlights & filters*; on Alerts, *Alerts* first — and
  ends with *Custom CSS*. A setting that makes no difference to what you have
  picked is hidden (the gradient angle only shows for a gradient, the frame
  fields only for a picture frame, and so on).
- **A live preview** on the right is the real overlay page drawing sample chat
  lines (a mod, a first-time chatter, a long line, an emote…) or a sample alert
  with whatever is in the form, saved or not. The size menu (**Small** to
  **Huge**, Large by default, remembered) sets how big it is; on a narrow window
  it sits above the settings instead. **Zoom in** shows the area that
  matters at a readable size and **Whole screen** shows all of 1920×1080;
  **Replay** plays the entrance again; the round buttons change the backdrop
  (dark, light, checkerboard, gameplay colours).
- Nothing reaches the stream until you press **Save**. **Revert** goes back to
  what is saved, and the panel warns you if you leave with unsaved changes.
  The **Send a test message** and **Test follow/sub/…** buttons fire the
  *saved* settings on the real overlay.

Everything is stored in `config/twitch.json` under `chat`, `events` and
`alerts`.

### Fonts

The font control is a picker, not a plain dropdown: **every font is listed in
its own face**, with a search box and categories (Sans-serif, Display & bold,
Gaming & sci-fi, Script & handwriting, Serif, Mono & pixel, Fun & themed,
Uploaded). About a hundred Google fonts are there to pick from.

- **Any other Google font** — type its name under the list (capitals count, for
  example `Bungee Inline`) and press **Use**. Hexcast asks Google for it first,
  so a typo is caught in the panel rather than on stream.
- **Your own font** — **Upload a font file** takes `.ttf`, `.otf`, `.woff` or
  `.woff2` (up to 10 MB). It is kept in `media/overlays/fonts/`, served by
  Hexcast, and listed under *Uploaded* by its file name (`My_Cool-Font.woff2`
  is picked as *My Cool Font*), so it works with no internet. Press *delete* on
  its row to remove it.
- Chat names have a font of their own (**Names → Font**, default *same as the
  message*), and so does an alert's label.

A Google font is fetched by the overlay itself, so the machine running OBS
needs internet for those; uploaded fonts do not.

### Text

Size, weight, line height, letter spacing, capitals (as typed / UPPERCASE /
lowercase / Capitalised) and colour for the message; names have their own size
(a percentage of the message), weight, capitals and colour (the viewer's own
colour, or one colour for everyone). Badge and emote sizes, timestamps and the
channel tag. **Outline & shadow** gives the text an outline and a drop shadow
whose colour, strength, blur and offset you set. On alerts the small **label**
line, the **main** line and the viewer's **message** each have their own
colour, weight and capitals, and the label its own font and letter spacing.

### Background

The **Style** dropdown does more than a flat colour. Chat and alerts have the
same set:

- **Solid** — the classic single colour + opacity.
- **Gradient** — blends the colour into a second colour at a chosen angle.
- **Animated gradient** — the same blend, slowly shifting.
- **Glass / frosted** — translucent panel with a hairline edge (the *Blur*
  field is the frosted blur; OBS can only blur what is on the overlay page
  itself, not your game behind it).
- **Outlined frame** — a solid box with a coloured border.
- **Neon glow** — a coloured outer glow, arcade-style.
- **Picture (fills the box)** — an image or animated GIF from your library.
- **Picture frame (9-slice)** — a frame image whose four corners stay crisp
  while its edges and middle stretch to fit.

Every style also takes a **Soft shadow** and a **Corner radius**.

**Pictures.** The *Picture* group appears for the two picture styles. Pick from
your library as a grid of thumbnails (**+ Upload** adds one; hover a thumbnail
and press × twice to delete it). Uploads — png, jpg, gif, webp, apng, up to
25 MB — are stored under `media/overlays/` and served from `/media/overlays/…`;
chat and alerts share the library. For the fill style:

- **Fit** — *Fill the box* (crops the edges), *Show all of it*, *Stretch to the
  box*, *Repeat as tiles*, or *Actual size, centred*. **Anchor** chooses which
  part stays in view when it crops.
- **Picture opacity** lets the background colour show through; **Darken the
  picture** lays black over it, so text stays readable on a busy image.

For the 9-slice frame:

- **Corner size in the file** — how far in from each edge of the *source image*
  the fixed corner ends (the thickness of the border art in the file).
- **Corner size on screen** — how thick that border renders. Match it to the
  file for a 1:1 look, or make it smaller for high-resolution art.
- **Edges** — *Stretch* (default) smears the edge strips; *Round* / *Repeat*
  tile them, which keeps the proportions of an ornate border.
- The centre of the file paints over the background colour, so a transparent
  middle lets your colour and opacity show through. Use a static PNG; animated
  GIFs do not reliably animate in 9-slice mode.

**Space inside the picture** adds room between the text and the artwork's edge.
A picture frame with no picture chosen is drawn as a solid box until you pick
one.

### Layout and placement

Most chat overlays live in a fixed box on the layout. Rather than resizing the
OBS browser source (which scales it and softens the text), the overlay is
**always the full OBS canvas** and you position things *inside* it from the
panel, at full fidelity. Make the browser source your whole screen, uncheck
**Shutdown source when not visible**, and forget about it — all sizing happens
in Hexcast.

The **Placement** card on the Layout sub-tab is a 16:9 picture of your screen
with two draggable, resizable boxes:

- **Chat** / **Alert** (blue) — where the text lives. Chat lines are clipped to
  this box, so chat stays put instead of spilling across the scene.
- **Background** (red) — the panel the **Style** paints. It moves and sizes
  independently, so you can sit the text in the lower half of a taller frame.
  With a picture style its width snaps to the picture's width and you drag its
  height.

Two switches: **Position … with these boxes** turns placement on (off, the
overlay behaves the classic way: chat flows over the whole source and the
background is per message; alerts are centred cards), and **Background fills
the whole screen** pins the background to the entire canvas.

With placement on, the Background style describes the *panel*; on chat, **Also
put a plain bubble behind each message** adds a solid bubble per line on top.
The Background sub-tab says which of the two it is styling.

Chat layout also has newest message at the top or bottom, alignment, the
width of the box, messages on screen, padding, the space between messages and
**Fade the oldest lines** (old lines melt away over that many pixels of the
box's edge). Alerts have the classic horizontal / vertical position, and the
space inside the card.

### Motion

Entrances: *Slide, Fade, Pop, Zoom in, Rise, Drop, From the right, Bounce, Blur
in, Flip up, None*, with a time. Exits: *Fade, Shrink, Float up, Sink,
Vanish*. Chat lines can leave on their own after a number of seconds; alerts
have their seconds on screen and the pause between alerts.

### Highlights and filters (chat)

A bar beside a **first-time chatter's** line, and — if you turn it on — beside
the **broadcaster, moderators, VIPs and subscribers**, each in a colour you
choose. Third-party emotes (7TV, BetterTTV, FrankerFaceZ), hiding `!` commands
and hiding bot accounts. Third-party emotes are fetched per channel and
globally; the channel sets need the numeric Twitch ID, so they only load once
you've signed in; global sets work either way.

### Custom CSS

The last sub-tab takes your own CSS, added to the overlay after everything
else (up to 20 000 characters), and it shows in the preview as you type. Chat:
`#wrap` (the box), `#bgbox` (the panel behind it), `.msg` (a line; also
`.msg.first` and `.role-mod`, `.role-vip`, `.role-sub`, `.role-broadcaster`),
and inside a line `.name`, `.badge`, `.emote`, `.mention`, `.cheer`, `.time`,
`.chan`, `.reply`. Alerts: `.alert` with `.kind-follow`, `.kind-subscribe`,
`.kind-cheer`, `.kind-raid`… for one kind, `#bgbox`, and inside `.title`,
`.body`, `.note`, `.pic`. For example `.kind-raid .body{ font-size:64px }`.

Both overlay pages and their shared `overlay.css` / `boot.js` are read from disk
on every request, so you can also edit them directly and just refresh.

---

## Alerts

The **Alerts** sub-tab lists every event type. Click a row to open it. The
switch on the left turns the alert on or off; **Test** fires it on the real
overlay (saved settings).

**What it says**

- **Title / Text** — templates supporting `{user} {amount} {tier} {months} {reward} {channel}`
- **Seconds on screen**
- **Soundboard clip** — a Hexcast clip name to fire alongside the alert
- **Smallest number of bits / gifted subs / raid size** — smaller ones show
  nothing (bits, gift subs and raids; 1 ignores nothing)

**How it looks** — each alert can differ from the others:

- **Accent colour** — its label colour and edge (default: the overlay's accent)
- **Entrance** — its own animation
- **A picture shown with it** — an emote, a logo, a GIF from your library, above,
  below or beside the text, at a height you choose
- **Background picture for this alert** — replaces the shared background
  picture (when the style is a picture style); the box keeps the shared size

The preview's drop-down picks which alert it shows; opening a row selects it.

The clip goes through the same `GET /api/play/{name}` endpoint the bot API uses,
so a raid can trigger an airhorn and a gif in the same beat. It is
fire-and-forget with a short timeout — a missing clip logs a failure and the
alert still shows.

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
  "fragments": [{"t": "text", "v": "hey there"}],
  "channel": "mainchannel",
  "channel_label": "primary"
}
```

Alerts arrive as:

```json
{"type": "event", "kind": "raid", "title": "Raid", "body": "SomeStreamer raided with 42",
 "user": "SomeStreamer", "amount": "42", "duration": 9.0,
 "channel": "mainchannel", "channel_label": "primary"}
```

`channel` is the login of the channel it came from (`channel_label` is its label,
or the login when it has none) — with several channels watched, that is how a bot
tells them apart; every channel is forwarded, whatever the overlays are set to show.

---

## Security

Same posture as the rest of Hexcast: no authentication. This module adds a
Twitch client secret and the OAuth tokens of every account you sign in with in `config/twitch_secrets.json`.
The API never returns either, but anyone who can reach port 4747 can
reconfigure your overlays and read your chat. Keep it on the LAN, and keep
`config/` out of git.

---

## Troubleshooting

**Panel says "chat only (anonymous)" after signing in.** The channel name
didn't resolve, or the token expired. Hit Reconnect and check the Status log.

**A second channel gets chat but no alerts.** It is running on your default
account, and Twitch only shows subs, bits, redeems and hype trains to the
channel's own account. Sign in as that channel's user (Connect a Twitch
account, as that user), then Reconnect.

**A channel never connects while others do.** Twitch allows three EventSub
connections per login; a fourth channel on the same login is refused. Give it a
login of its own, or turn another channel off.

**A subscription failed with 403.** Missing scope. Sign out and back in so
Twitch re-prompts for the full list.

**A subscription failed with 400 "invalid subscription type and version".**
Twitch has retired that version of the event. The subscription list in
`plugins/twitch/twitch.py` (`SUB_PLAN`) carries a version string per type; check the current
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
