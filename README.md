# Hexcast

Self-hosted, completely free stream tools that push audio and video to an OBS
overlay — with full control over position, scale, start/stop times, volume,
delay, and more. **No data collection, no monthly fee, no sign-ups, always
free.**

---

## ⚠️ Security: local network use only

**Hexcast has no authentication of any kind. It is designed to run on a trusted
home/studio LAN — never expose it to the public internet.**

Anyone who can reach the port can:
- trigger any media into your stream,
- **upload arbitrary files** to your machine (the upload endpoint accepts files and writes them to disk),
- **delete any media** in your library,
- enumerate your entire library via the API,
- **install, update or remove plugins** from the catalog (the **+** tab) — which downloads the Python packages a plugin needs.

There is no rate limiting and no input gating beyond file-extension checks. Do
**not** port-forward this, do **not** put it on a public VPS, and do **not**
assume "nobody knows the URL" protects you.

**Safe ways to run it:**
- On the same machine as OBS, reached only via `localhost` (set the bind host to `127.0.0.1` near the bottom of `hexcast.py`).
- On a LAN, with a firewall rule restricting the port to your local subnet.
- If you genuinely need remote access, put it behind a reverse proxy (nginx/Caddy) that enforces authentication and TLS, on a private network or VPN — that's on you to set up correctly.

The plugins store their secrets under `config/` (a Twitch OAuth token +
client secret, a YouTube Music pairing token, a Discord RPC token). Keep that
folder out of git and keep the port on your LAN. A plugin is code that runs on your
PC with your permissions: install the ones that ship with Hexcast, and add a
[remote catalog](docs/plugins.md#catalogs) only if you trust whoever runs it.

---

## What can Hexcast do?

At its core, Hexcast is a **soundboard + media launcher** that drives a single
set of OBS browser-source overlays from a web control panel. **That is all you
get on a fresh download** — one tab, **Soundboard**, and a **+** next to it.

Everything else is a **plugin** you add with one click from that **+** tab:
Twitch, Music, Avatars, Discord, Clips, Countdown, Games and Ticker. Each is
self-contained, with its own overlay and settings panel, and gets its own tab in
the top bar once installed. Add as many or as few as you like; none of them
changes how the soundboard behaves, and a plugin you never install costs you
nothing — not even its Python packages. **Games** goes one level further: each
game (Roulette, Craps, Russian Roulette, Trivia, Hexfall, Soul Climb, Blackjack) is its own add-on, installed from
the **+** inside the Games tab. See [docs/plugins.md](docs/plugins.md).

Everything runs on your own machine and streams to OBS over your LAN.

### 🔊 Soundboard (the core)

**What it does.** Trigger audio and video clips into your stream with a click.
Clips **layer** — spam them and they all fire at once (unless you set a per-clip
cooldown). A **⏹ Stop All** panic button clears every visual and stops every
sound instantly.

Each clip has an in-panel **editor**:
- **Video** — drag-to-position on a 16:9 canvas preview (or a 3×3 quick grid), scale, volume (for clips with an audio track), a dual-thumb **start/end trim** with live **▶ Preview**, per-clip **chroma key** (color + tolerance + eyedropper, keyed inside Hexcast so overlapping green-screen clips composite cleanly), rename, and a per-clip **cooldown**.
- **Audio** — volume, trim, rename, cooldown, with preview through your speakers.

**Edit Mode** and **Delete Mode** let you tune or remove clips without touching
the filesystem.

**How it works.** A small local web server hosts the control panel at
`http://localhost:4747/` and drives the overlay at `/overlay` over a websocket.
Media lives in `media/audio/` and `media/video/`; a folder watcher imports new
files instantly, drag-and-drop uploads route by type, and animated
`.gif`/`.webp`/`.apng` files auto-convert to seekable `.mp4`. Per-clip settings
save to a small sidecar JSON next to the file. Anything can also be triggered by
name over a simple HTTP **[Bot API](#bot-api)** — great for chat bots and
stream-deck buttons.

> **This `/overlay` is where every soundboard clip plays — including clips fired
> by plugins** (Twitch alerts, Countdown cues, a Music track-change clip,
> Games launch/landing clips).
> Those plugins trigger the soundboard rather than drawing on their own
> overlays, so keep the base `/overlay` source in your scene alongside whatever
> plugin overlays you're using.

### 💬 Twitch *(plugin)*

**What it does.** Adds a **chat overlay** and an **alert overlay** for follows,
subs, gift subs, bits, raids, channel-point redeems, and hype trains, with a
FIFO alert queue. Any alert can **fire a soundboard clip by name** — just put
the clip in the Clip column of the Alerts table — so you don't need a separate
bot for sound alerts. (Those clips play on the base soundboard overlay
(`/overlay`), so keep it in your scene alongside the Twitch overlays.) The
**`!so` shoutout** command fires the official Twitch shoutout banner, a
configurable chat line, and a random clip of that channel at the **Clips**
overlay — all in one.

**How it works.** You sign in from the Twitch panel; Hexcast subscribes to
Twitch EventSub and renders the overlays. It can also POST every event to a
forward URL if you want your own bot or a model reacting to chat.
See [docs/twitch.md](docs/twitch.md).

### 🎵 Music *(plugin)*

**What it does.** A now-playing overlay — album art, live progress, an accent
colour pulled from the artwork, and an audio visualiser — from one of **two
sources you pick in the panel**:

- **YouTube Music** — mirrors the [YouTube Music Desktop App](https://ytmdesktop.github.io/), optionally showing the music video in the card. The video is looked up with yt-dlp and streamed through Hexcast (nothing saved to disk, no YouTube player controls), the next song's video is prepared ahead, and each new song is briefly held until its video is ready so the two start in step. See [how video loading works](docs/music.md#how-video-loading-works-step-by-step).
- **Local files** — point it at a music folder (**Browse…** opens your OS's native folder picker), then browse or **search-as-you-type** across a library of **any size**, build a **queue** (multi-select, "add whole folder + subfolders", drag-to-reorder, virtualised so 15k tracks scroll smoothly), save queues as reusable **playlists** (load to replace or append), and **preview** tracks right in the panel while you curate. Local audio plays *inside the overlay* so OBS captures it, and the visualiser reacts to the real audio.

**How it works.** YouTube Music comes over the desktop app's companion server.
For local files, Hexcast builds a **cached index of your folder** — saved to disk
and loaded instantly on every launch, so you only press **Re-index** when the
files on disk actually change. That makes folder browsing and type-ahead search
(filtered in the browser over the cached index) instant even on tens of thousands
of tracks. Files are served with HTTP range requests for instant seeking and
played by an `<audio>` element in the overlay with a Web Audio visualiser. A
plain-text `!song` endpoint is included for chat bots. See [docs/music.md](docs/music.md).

**Optional YouTube sign-in (music videos).** Off until you click **Link** on the
Music page. Linking saves only the *name* of a browser (Firefox/Chrome); when
YouTube refuses an anonymous lookup (age-restricted, bot check), yt-dlp reads
that browser's YouTube cookies on this PC for that lookup, so it's done as your
account. Stays linked across restarts until **Unlink**. Separate from the Clips
link. Details: [docs/music.md](docs/music.md#youtube-sign-in-optional).

### 🎭 Avatars *(plugin)*

**What it does.** Live2D avatars your AI drives through the API — a lighter
VTube Studio without face tracking: **the bot is the tracker**. Any number of
models on screen, each addressed by a name (`main`, `guest` …), placed by dragging
and scrolling in a live preview that is the OBS renderer itself. Between commands
each one stays alive — blinking, breathing, idle sway, wandering eyes, head motion
while talking.

**What a bot can do.** Speak a line (it hands over the audio; the overlay plays it
with **advanced lipsync** — vowel shapes, VTube Studio's `VoiceA`…`VoiceO`), or
lip-sync from an audio device / virtual cable, or move the mouth itself. Set **any
Live2D parameter** (held, eased, weighted, or streamed every frame over a
WebSocket), toggle the model's **expressions** (several at once), play its
**motions**, set an **emotion** or face, **look** at a point, **nod / shake /
tilt**, move / zoom / hide the avatar, add **items** (pictures, GIFs, frame
animations, Live2D items — pinned to the head or a hand if you like) and change
its **light** (key, rim, ambient, animated presets). Events tell it when a line
has finished.

**Models.** Cubism 3, 4 and 5 models from a zip or a folder — or straight from
VTube Studio if it's installed, with their VTube Studio parameter setup, hotkey
expressions, idle animation and art mesh colours. **PNGtubers** too: a few pictures (quiet, talking,
blinking — per state, optionally a mouth per vowel), or a layered **PNGTuber Plus**
avatar imported from its `.save`, with the same API. Live2D's own runtime is downloaded once after you
accept Live2D's license in the tab (it doesn't ship with Hexcast).
See [docs/avatar.md](docs/avatar.md).

### 🎙️ Discord *(plugin)*

**What it does.** A voice-reactive overlay: everyone in your current Discord
voice channel appears in OBS and lights up as they speak — using Discord avatars
or custom PNGTuber-style idle/talking image pairs.

**How it works.** It talks to the Discord **desktop app's local RPC** — no bot,
no server-side token setup beyond authorizing once.
See [docs/discord.md](docs/discord.md).

### 🎬 Clips *(plugin)*

**What it does.** Queue up Twitch clip/VOD links — paste a single URL or a whole
blob of chat — then fire them **one at a time** to a full-window overlay (no
auto-advance, so you stay in control). Bots can trigger items by number, and the
Twitch **`!so` shoutouts** play through this same overlay. An optional
**auto-leveler** brings every clip (and shoutout) in at a consistent volume, so
one clip doesn't blast while the next whispers.

**How it works.** yt-dlp resolves direct MP4/HLS playback, with optional
pre-download so a clip is ready the instant you play it. With **auto-level** on,
the server measures each clip's loudness with ffmpeg (streamed through it —
nothing is downloaded or saved) and the overlay turns louder clips down toward a
target loudness (default −16 LUFS). It normalises *toward* the target, so it
never adds start-up lag; a clip that plays before it's measured (e.g. an instant
shoutout) corrects its volume a moment in. See [docs/clips.md](docs/clips.md).

**Optional YouTube sign-in (clips).** Off until you click **Link** on the Clips
page. Same mechanism as the Music one, but a separate setting: only the
browser's *name* is saved, cookies are read by yt-dlp at lookup time and only
when YouTube refuses anonymously. Stays linked until **Unlink**. Details:
[docs/clips.md](docs/clips.md#youtube-sign-in-optional).

### ⏱️ Countdown *(plugin)*

**What it does.** A fully styleable countdown timer overlay — count down a fixed
duration or to a wall-clock time — positioned on a 1920×1080 stage like the chat
window. **Media cues** can fire any number of soundboard clips, each anchored to
its own countdown threshold: set a clip to *end* at a point (e.g. intro music
finishing exactly at 0:00, timed backwards from the clip's length) or to *start*
at a point.

**How it works.** The server owns the authoritative remaining time and streams
it to the overlay, so clock skew between machines never matters; cues are
computed against that timer and fired through the soundboard. **Those clips play
on the base soundboard overlay (`/overlay`), not the countdown overlay** — keep
both browser sources in your scene. See [docs/countdown.md](docs/countdown.md).

### 🎰 Games *(plugin, with one add-on per game)*

**What it does.** Bot-driven casino games on stream, each on its own tab of
the Games panel and placed with the same **Edit Mode** as the soundboard:

- **Roulette** — a casino wheel pops onto the overlay, spins, launches the ball
  the other way, and the ball slows, drops, bounces off the deflectors and
  settles in a pocket — then the result pops up with a history strip of recent
  numbers. It's a standard **American double-zero** wheel (38 pockets) in one
  of four themes. Chat bots can send **bets** with a spin (straight, split,
  street, corner, dozens, columns, red/black and the rest) and get every one
  back resolved at standard odds, ready to pay out — or put chat's bets on
  a **table** for the next spin, like craps: the bets show on an "on the
  table" board while a **spin timer** counts down, the wheel spins by itself
  at zero, and the winnings go through the same **ledger** as craps.
- **Craps** — a bank-craps tray where chat bets the channel currency
  (**hexcoins**) on a table that persists across rolls: pass / don't pass,
  come / don't come, free odds, place bets, hard ways, the field and the
  one-roll props, all at standard casino odds. The dice are thrown in, bounce
  off the diamond back wall and land on the server's faces; the stickman calls
  the roll, the puck moves, bets settle, and an optional **auto-roll** (or a
  **roll timer** started on demand) keeps the game going on its own. Hexcast
  holds no balances — the channel's bot (**Hex**, with its hexbank) is the
  **bank**, and a numbered, persisted **ledger** tells it exactly what to
  take and pay, even across restarts. See [docs/craps.md](docs/craps.md).
- **Russian Roulette** — a side-on revolver and a stuffed burlap dummy with a
  name tag (a chatter who volunteered, who earns a cut of the bank's win).
  Pull k loads k bullets and re-spins; chat bets the dummy **survives** (the
  stake rides and grows from pull to pull — cash out or let it ride) or goes
  **bang** this pull. On a bang the stuffing bursts out and the dummy crumples
  off its post, then gets re-stuffed for the next game. Three pulls, ~1:30 a
  game. See [docs/russian_roulette.md](docs/russian_roulette.md).
- **Trivia** — a game-show board with hexagon lozenges: chat bets **before**
  seeing each question (only its category and difficulty), answers A–E, sees
  how many picked each option, then the right one lights up green and the
  wrong ones drop away. Winners ride their balance for a streak bonus or cash
  out. 15 questions from easy to hard, from **Open Trivia DB** and your own
  lore questions, never repeated in a night. See
  [docs/trivia.md](docs/trivia.md).
- **Hexfall** — a hex-themed plinko: one glowing token drops through a pyramid of
  hexagonal pegs into slots of **multipliers** and **busts**. Chat puts up a bet
  before every drop and the landing slot pays it (or doesn't). Three drops a
  game. See [docs/hexfall.md](docs/hexfall.md).
- **Soul Climb** — a damned soul climbs out of a hell pit while chat bets
  **how high he gets**: pass your height and you're paid its multiplier, fall
  short and the stake is gone. Near-falls, demons, bats, a skeleton hand and a
  different way to fall every time. See [docs/climb.md](docs/climb.md).
- **Blackjack** — a half-moon felt table with Hex as the dealer, Vegas rules, a
  shoe that grows with the player count (2 to 8 decks), up to 14 seats and a
  queue. Everyone acts at once in short action rounds; names, cards and chip
  stacks sit at each seat, and you keep your seat by ante-ing up each hand.
  See [docs/blackjack.md](docs/blackjack.md).

All the round games (Russian Roulette, Trivia, Hexfall, Soul Climb, Blackjack) are placed and styled in the same **Edit Mode** editor as
roulette and craps (sample game, ▶ Preview, **Test in OBS**), and carry your
branding, not ours: the title (`title`: "Russian Roulette" / "TRIVIA") and
trivia's name for your own questions (`lore_label`: "Channel Lore") are
settings.

Soundboard clips can fire when the ball or dice are thrown and when they land.

**How it works.** The server picks the pocket, or rolls the dice, with a
cryptographic RNG (`secrets.SystemRandom`) — every pocket and every die face
equally likely. The overlay only animates the server's result, and nothing in
the API or panel can force an outcome. The
animation is seeded, so every overlay (and the panel's live preview) shows the
identical spin or roll, and a source that reconnects mid-spin rejoins at the
right moment. A spin or roll is one HTTP call, and `wait=true` holds the
response until it lands. **Launch/landing clips play on the base soundboard
overlay (`/overlay`)** — keep both browser sources in your scene. See
[docs/games.md](docs/games.md), [docs/craps.md](docs/craps.md),
[docs/russian_roulette.md](docs/russian_roulette.md), [docs/trivia.md](docs/trivia.md),
[docs/hexfall.md](docs/hexfall.md), [docs/climb.md](docs/climb.md) and [docs/blackjack.md](docs/blackjack.md).
Every bet in every game is against the bank (the bot), paid through the same
ledger; Hexcast never reads chat — your bot turns chat commands into API calls,
so any AI vtuber's bot can run them.

### 📰 Ticker *(plugin)*

**What it does.** A news-style scrolling ticker that you place anywhere on the
1920×1080 stage by dragging a box over a live preview. You can style every part
of it: font, size, colors, separator, feed label badges, a pinned title, a
solid, gradient or glass bar, and bar and ticker transparency. What scrolls is
a set of named **feeds** (for example `news`, `balances` or `announce`). You can
edit the feeds in the panel, and a bot can write them over HTTP, one line or a
whole feed at a time. Lines with a `key` update in place, so every viewer's
hexcoin balance can scroll and stay current.

**How it works.** The overlay takes the next line from the live list as each
one scrolls off, so changes land mid-scroll without a restart. New lines jump
the queue. Feeds can also come from `config/ticker_feeds.json`, which is
watched and reloaded within a second, or from a polled JSON URL. See
[docs/ticker.md](docs/ticker.md).

---

## Install, upgrade & uninstall

### What you need

- **Python 3.10+** (uses modern type-union syntax).
- **OBS Studio 28+** with browser-source support.
- **Deno** — *optional*, only for the YouTube sign-in in the Music and Clips plugins (signed-in lookups make yt-dlp solve a JavaScript challenge). Windows: `winget install DenoLand.Deno`; Node or Bun also work if already installed. yt-dlp itself and its solver scripts (`yt-dlp-ejs`) install automatically with the other requirements.
- **ffmpeg** — *optional but recommended*. It powers gif/webp → mp4 conversion, thumbnails, media-duration/audio probing, and the Music tab's local tag/cover-art reading. Hexcast runs without it, but animated GIFs won't be seekable, thumbnails won't generate, the 🔊 audio badge won't appear, and local music shows filenames only.

### Install — the easy way

Hexcast is meant to be **double-click-and-go**; you don't need to know anything
about Python. The launcher (`start.bat` on Windows, `start.sh` on Mac/Linux)
checks everything, sets itself up, downloads what it needs, and tells you in
plain English if something's missing.

**Windows**
1. **Install Python** (one time). Open <https://www.python.org/downloads/>, click **Download Python**, run the installer, and on the first screen **tick "Add python.exe to PATH"** before clicking **Install Now**.
2. **Get Hexcast.** On [the GitHub page](https://github.com/UMDSmith/hexcast), click **`< > Code` → Download ZIP**, then right-click → **Extract All…** to a permanent spot like your Documents folder. *(Prefer git? `git clone https://github.com/UMDSmith/hexcast.git` makes updating a one-liner.)*
3. **Run it.** Double-click **`start.bat`**. First launch takes a minute while it sets up; later launches start in seconds. When the window shows `Control panel: http://localhost:4747/`, open that address in your browser.

   > If **Windows SmartScreen** appears, click **More info → Run anyway** — it's a plain-text launcher you can open in Notepad.

**Mac / Linux**
1. Install Python 3.10+ — macOS: `brew install python`; Ubuntu/Debian: `sudo apt install python3 python3-venv`.
2. Get Hexcast: `git clone https://github.com/UMDSmith/hexcast.git && cd hexcast` (or download + extract the ZIP).
3. Run it:
   ```bash
   chmod +x start.sh   # first time only
   ./start.sh
   ```
   Open `http://localhost:4747/` when it prints the address.

Leave the launcher window open while you stream; close it to stop Hexcast.

### Set it up in OBS

1. In OBS: **Sources → + → Browser**.
2. **URL:** `http://localhost:4747/overlay` · **Width/Height:** match your canvas (typically 1920×1080).
3. **Control audio via OBS:** ON (routes sound through your mixer). **Shutdown source when not visible:** OFF (otherwise the websocket dies). **Refresh when scene becomes active:** OFF.
4. Select the source and press **Ctrl+F** to fit.

Each plugin you add brings its own browser source (e.g. `/ytm/overlay`,
`/twitch/chat`, `/countdown/overlay`, `/games/overlay`, `/ticker/overlay`); its panel shows the
exact URL.

### Add plugins

Open the control panel and click the **+** tab in the top bar. Every plugin is a card:
press **Install** and it downloads what it needs (a progress log shows what it's doing), starts,
and its tab appears — **no restart**. Some plugins need another one and bring it along
(Music and Clips share a small *yt-dlp helpers* plugin). Later, each card offers **Update**
(when a newer copy is available), **Turn off** and **Remove** — removing keeps your settings,
so adding the plugin back picks up where you left off.

Games: install **Games**, open its tab, and click the **+** at the end of *its* tabs to pick the
games you want. Prefer a terminal? `python hexcast.py plugins list` / `install twitch music` /
`update --all` / `remove clips`. Everything about plugins — including how to write your own — is in
[docs/plugins.md](docs/plugins.md).

### Install — advanced / manual

The launchers aren't required:

```bash
# Windows: python -m venv .venv && .venv\Scripts\activate
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python hexcast.py
```

That installs just the core. Add plugins from the **+** tab, or from the command line — each one
installs its own Python packages, so nothing is downloaded for a plugin you don't use:

```bash
python hexcast.py plugins list
python hexcast.py plugins install twitch music discord
python hexcast.py plugins install games games_roulette games_craps games_russian games_trivia games_hexfall games_climb games_blackjack
python hexcast.py plugins install --all
```

The one separate optional extra is the Music plugin's **"react to real audio" visualiser**, which needs
`numpy` + `soundcard`: `pip install -r plugins/music/requirements-audio.txt`.

### Install — Docker

```bash
docker build -t hexcast:latest .          # add --platform linux/amd64|arm64|arm/v7 to cross-build
mkdir -p ./hexcast-media/audio ./hexcast-media/video ./hexcast-config
docker run -d --name hexcast -p 4747:4747 -v ./hexcast-media:/app/media -v ./hexcast-config:/app/config hexcast:latest
```

The image bundles ffmpeg. The `-v` mounts persist your library and your settings. The image starts with just the
soundboard; **bake in the plugins you use** so they survive a rebuild or a new container:
`docker build --build-arg PLUGINS="twitch music games games_roulette" -t hexcast:latest .` (`PLUGINS=all` for everything).
Plugins added from the **+** tab inside a running container live only in that container. Control panel at
`http://localhost:4747/`; OBS source at `http://<your-machine-ip>:4747/overlay`.
For a registry multiarch push: `docker buildx build --platform
linux/amd64,linux/arm64,linux/arm/v7 -t your-registry/hexcast:latest --push .`.
**Still LAN-only — don't expose port 4747 to the internet.**

### Installing ffmpeg

- **Debian/Ubuntu:** `sudo apt install ffmpeg` · **Fedora:** `sudo dnf install ffmpeg` · **macOS:** `brew install ffmpeg`
- **Windows:** download "release essentials" from <https://www.gyan.dev/ffmpeg/builds/>, extract to e.g. `C:\ffmpeg\`, add `C:\ffmpeg\bin` to your PATH (System Properties → Environment Variables → Path → New), open a new terminal and verify with `ffmpeg -version`.

### Upgrading

Your library (`media/`), all settings/secrets (`config/`) and the plugins you installed (`plugins/`) are git-ignored, so
updating never touches them.

**What the top bar shows.** At the top right of every page: `Hexcast Version: 2.0`, and next to it the version of the
module (plugin) of the tab you are on - `Games Version: 1.0`, `Twitch Version: 1.0` (nothing extra on the Soundboard).
`1.0.0` is shown as `1.0`; anything else as it is (`1.2.3`). Inside Games, each game's tab shows its own version
(`Craps Version: 1.0`) and each game card in the game store shows it too. On a narrow window the word *Version* is dropped.

**Update links.** When a newer version exists, an **Update to 1.1** button appears: next to that module's version in the
top bar, on the game's tab in Games, on its card in the **+** store, and as a green dot on the **+** tab. Pressing it updates
*that module only* - the new files and Python packages are prepared while the old version keeps running, your settings
are kept, nothing needs restarting, and if anything goes wrong the old version stays. For Hexcast itself the top bar shows
`Hexcast Version: 2.0` with **Update to 2.1**, a link to the GitHub ZIP: download it and unpack it over your Hexcast folder
(Hexcast never overwrites its own core files; your `media/`, `config/` and `plugins/` are not in the ZIP).

**No git needed.** Hexcast checks GitHub itself:
- **How often:** at most once an hour. The answer is cached, and the check runs in the background - it never delays start-up
  or a page load, and when you are offline it just stays quiet.
- **What is fetched:** the repository's `VERSION` file (the newest Hexcast) and the small `plugin.json` of each module you have
  installed (to read its version). When you press **Update to ...** on a module, its folder is downloaded from the repository
  ZIP (https, github.com only, size-capped, nothing outside that module's folder is unpacked). Nothing about you, your
  streams or your settings is sent - it is the same as opening those files in a browser.
- **Turn it off:** click the **gear** at the top right of the top bar (Settings) and switch **Check GitHub for updates** off
  (or put `{"check_updates": false}` in `config/plugins.json`). Then Hexcast makes no update requests at all; version numbers
  still show, and updates that arrive in the local `catalog/` folder (git users: `git pull`) are still offered. The same page
  has **Check now** (with the time of the last check and what it found) and the repository / branch to follow (default
  `UMDSmith/hexcast`, `main`).

More in [docs/plugins.md](docs/plugins.md#versions-and-updates).

- **Git:** `git pull`, then run the launcher.
- **ZIP:** download the latest from **`< > Code`** and extract over your existing folder (keep `media/`, `config/` and `plugins/`), then run the launcher.
- **Docker:** `git pull && docker build -t hexcast:latest . && docker stop hexcast && docker rm hexcast`, then re-run the `docker run …` command above.

The launcher notices when requirements changed and re-installs automatically —
no manual `pip` to remember.

**Plugins update on your say-so.** An update brings a newer copy of the plugin folder into `catalog/` (or is fetched from GitHub), but what
runs is the installed copy in `plugins/` — a plugin's card shows **Update to 1.1** when something newer exists, and
pressing it swaps the files and restarts just that plugin, so nothing changes in the middle of a stream.

**Coming from a version without plugins?** Nothing to do: the first start keeps every tab you were already using (it
looks for their settings in `config/` and installs those plugins for you). A tab you never configured is one click away in **+**.

### Uninstalling

Hexcast is self-contained — everything lives inside its folder.

1. Stop it (close the launcher window, or `docker stop hexcast && docker rm hexcast && docker rmi hexcast:latest`).
2. Delete the `hexcast` folder — the app, its `.venv`, your `media/`, `config/` and installed `plugins/` all live there; nothing is installed elsewhere. (Back up `media/` and `config/` first if you want to keep them.)
3. Remove the browser source(s) you added in OBS.
4. Optional: uninstall Python and ffmpeg if you added them only for Hexcast.

### Caveats

- **Security:** LAN-only, no auth — see the **⚠️ Security** warning at the top. Plugin tokens live under `config/`; keep it private.
- **ffmpeg** is optional but strongly recommended (see above for what you lose without it).
- **Local music playback** happens in the OBS overlay browser source, so open `/ytm/overlay` (in OBS or a browser) to actually hear it; the panel's **▸ Preview** is a local audition only. Playback is limited to codecs the browser engine supports (MP3, M4A/AAC, OGG/Opus, FLAC, WAV all work; exotic formats like WMA may not).

---

## Reference

### Bot API

Trigger media over plain HTTP — for chat bots, stream decks, scripts, anything.
**No authentication** (trusted-LAN use).

```
GET  /api                          → endpoint reference
GET  /api/list                     → JSON: { audio: [...], video: [...] }
GET|POST /api/play/{name}          → fuzzy: searches audio, then video
GET|POST /api/play/{kind}/{name}   → explicit: kind = audio | video
     ?x=&y=&scale=                 → optional position override (video)
     ?volume=                      → optional volume override 0.0–1.0
     ?start=&end=                  → optional trim window in seconds
     ?avatar=&anchor=&dx=&dy=      → video: play it glued to an avatar (Avatars plugin); ?avatar= alone (empty) plays it here
GET|POST /api/stop                 → clear all visuals + stop all audio (panic)
POST /rename                       → {file, kind, new_stem} → rename media + sidecar + poster
POST /attach                       → {file, kind: "video", attach: {avatar, anchor, ...} | null} → lock a clip to an avatar
```

Names are case-insensitive and match the filename stem (`airhorn`) or full name
(`airhorn.mp3`). Saved editor values (position/scale/volume/trim) apply
automatically. If a clip has a non-zero `cooldown_ms`, triggers during its
cooldown return `{"ok": true, "delivered": 0, "suppressed": true, "next_in_ms": N}`.
A video locked to an avatar plays on the Avatars overlay that shows it, and the answer
adds `"attached": true`; when no open overlay shows that avatar it plays here as usual
(`"attached": false`).

```bash
curl http://localhost:4747/api/play/airhorn
curl "http://localhost:4747/api/play/video/cheer?x=80&y=20&scale=2&volume=0.6&end=3"
curl http://localhost:4747/api/stop
```

For Twitch redemptions, the Twitch integration handles this natively (no bot
needed); or map a redemption/command to a clip name and call `/api/play/{name}`
from your own bot.

**Games (roulette)** — same idea, every call returns `{"ok": true|false, ...}`:

```
GET  /games/api                                → games endpoint reference
GET|POST /games/api/roulette/spin              → spin (alias /play); result + resolved bets
     ?user=&duration=                          → caption name (≤ 40 chars) · spin seconds 4–30
     ?wait=true                                → respond when the ball lands
     ?test=true                                → not recorded, no clip cues, the table untouched
     ?bet=red&amount=100                       → one bet (POST {"bets": [...]} for up to 200)
     ?x=&y=&scale=                             → per-spin placement (POST "overrides" for more)
GET  /games/api/roulette/last                  → last committed spin
GET  /games/api/roulette/history?limit=20      → recent spins + stats (hot/cold, streak, counts)
POST /games/api/roulette/history/clear         → clear history + stats
GET  /games/api/roulette/validate?bet=17/20    → parse a bet: type, numbers, odds, or error
GET  /games/api/roulette/bets                  → bet-type reference (syntax, odds)
GET|POST /games/api/roulette/show | hide       → idle wheel on / off screen
GET|POST /games/api/roulette/announce          → winners card when the bot did the math (display only)
     {title, lines:[{user,amount,text}], ...}  → ≤ 50 lines · empty_text · seconds 1–120 · spin_id · currency
     ?user=&amount=&text=                      → one line (query shorthand)
GET|POST /games/api/roulette/announce/clear    → take the card down now (the next spin clears it too)
GET|POST /games/api/roulette/bet               → {user, bet, amount} or {"bets": [...]} (≤ 200) on the table
                                               → accepted, rejected, debits [{user, amount, bet_id, seq, reason}]
GET|POST /games/api/roulette/remove            → {bet_id} | {user, bet} | {user, all: true} → credits (refund)
GET  /games/api/roulette/table                 → bets, exposure, auto_spin_in_ms (the countdown), last_seq
GET  /games/api/roulette/user/{name}           → one viewer's table bets, exposure, session totals
GET|POST /games/api/roulette/clear             → refund every table bet, stop the countdown
GET|POST /games/api/roulette/board · board/clear → Hex's own "on the table" board (≤ 100 lines) · the table's again
GET  /games/api/roulette/ledger?since=0&limit=500 → roulette's debits/credits after a seq
GET|POST /games/api/{game}/timer?seconds=30    → start the countdown now (5–300 s, default the bet window);
                                                 at 0 the wheel spins / the dice roll by themselves
GET|POST /games/api/{game}/timer/cancel        → stop the countdown (auto-spin / auto-roll or /timer)
GET  /games/api/ledger?since=0&game=           → every game's debits/credits in one stream (or one game's)
GET|POST /games/api/stop                       → abort + hide everything (games only)
GET  /games/api/status · GET|POST /games/api/config
```

A spin (or a `/timer`) while another spin is still spinning, showing its
result or in cooldown returns HTTP 409
`{"ok": false, "error": "busy", "retry_in_ms": N, "state": "..."}`; a table
bet, take-down or clear while the ball is in the air returns 409
`{"ok": false, "error": "bets_closed", "retry_in_ms": N}`.
Bet syntax, odds and bot recipes: [docs/games.md](docs/games.md). Hooking
Hex's hexbank up to roulette: with bets on the spin call (take stakes, pay
each bet's `returned` once), [Hooking up the bank](docs/games.md#hooking-up-the-bank-hexcoins);
with the table and the spin timer (take each bet's `debits`, pay from the
ledger), [Table & spin timer](docs/games.md#table--spin-timer).

**Two ways to run the money.** Either Hexcast does the bet math (roulette:
bets sent with the spin, or the table and its ledger; craps' table and
ledger) or the bot does it in its own code — here that's Hex, with its
hexbank — and only tells the overlay what to show with `/announce` and
`/board`. The outcome is always Hexcast's fair spin or roll; a bot can't send
one. An announce for an old spin gets
409 `{"ok": false, "error": "stale", "spin_id": "..."}`. See
[docs/games.md](docs/games.md#two-ways-to-run-the-money).

```bash
curl "http://localhost:4747/games/api/roulette/spin?user=bob&bet=red&amount=100&wait=true"
curl "http://localhost:4747/games/api/roulette/validate?bet=split:17/20"
curl "http://localhost:4747/games/api/roulette/bet?user=alice&bet=red&amount=100"
curl "http://localhost:4747/games/api/roulette/timer?seconds=30"
curl "http://localhost:4747/games/api/ledger?since=0"
```

**Games (craps)** — bets stay on the table across rolls; the bot (Hex) is the
bank: it takes each accepted bet's `debits` and pays credits from the ledger:

```
GET|POST /games/api/craps/roll                 → throw the dice (alias /spin, /play); settlements + credits
     ?user=&duration=                          → shooter (if none yet) · roll seconds 2.5–10
     ?wait=true · ?test=true                   → respond when the dice land · animation only (no settlement)
GET|POST /games/api/craps/bet                  → {user, bet, amount, target?} or {"bets": [...]} (≤ 200)
                                               → accepted, rejected, debits [{user, amount, bet_id, seq}]
GET|POST /games/api/craps/remove               → {bet_id} | {user, bet} | {user, all: true} → credits (refund)
GET  /games/api/craps/table                    → phase, point, shooter, bets, exposure, last_seq
GET  /games/api/craps/user/{name}              → one viewer's bets, exposure, session totals
GET  /games/api/craps/ledger?since=0&limit=500 → craps' debits/credits after a seq (tail it by seq)
GET|POST /games/api/craps/clear                → refund every bet, reset to come-out
GET  /games/api/craps/validate?bet=&user=&amount=&target=
                                               → dry-run a bet: odds, max odds, hint, or error
GET  /games/api/craps/bets                     → bet reference (syntax, pays, when)
GET|POST /games/api/craps/announce             → payouts card when Hex did the math (same fields as roulette)
GET|POST /games/api/craps/board                → {title, bets:[{user,text,amount}]} (≤ 100) replaces the bets board
     {"bets": []} · {"clear": true}            → nothing down (no board) · back to the table's own board
GET|POST /games/api/craps/announce/clear · board/clear
GET|POST /games/api/craps/timer · timer/cancel → start the countdown to the next roll now · stop it
GET  /games/api/craps/last | history · GET|POST show | hide | stop
```

A bet, take-down or clear while the dice are in the air returns HTTP 409
`{"ok": false, "error": "bets_closed", "retry_in_ms": N}`; a roll (or a
`/timer`) during the previous roll, its result or cooldown returns 409 `busy`
like roulette. The ledger is shared with roulette's table — one `seq`
numbering, each event names its `game` — so a bank running both tails
`/games/api/ledger` once.

**Games (Russian Roulette)** — one game at a time; bets against the bank, paid
through the same ledger:

```
GET|POST /games/api/russian/start              → {dummy, dummy_user, rounds, seconds, test} → a game (409 if one runs)
GET|POST /games/api/russian/bet                → {user, side: survive|bang, amount} → debits (409 bets_closed)
GET|POST /games/api/russian/cashout            → {user} → the survive stake's value now (credit)
GET|POST /games/api/russian/remove             → {user, side?} → take back this window's bets (refund)
GET|POST /games/api/russian/dummy              → {dummy, dummy_user} → this game (before pull 1) or the next
GET|POST /games/api/russian/next               → end the current phase now (alias /pull)
GET  /games/api/russian/table · user/{name} · ledger?since=0 · history · bets
GET|POST /games/api/russian/stop               → end it: stakes refunded, survive stakes cashed out
GET|POST /games/api/russian/preview            → {overrides, seconds} → Test in OBS: that look on screen for a few seconds (never the game)
```

**Games (Hexfall, Soul Climb, Blackjack)** — round games with the same shape as Russian Roulette's API above
(`/start`, `/bet`, `/remove`, `/next`, `/stop`, `/preview`, `/table`, `/user/{name}`, `/ledger`, `/history`, `/bets`):

```
GET|POST /games/api/hexfall/bet                → {user, amount} → debits; the next drop pays amount × the landing slot's multiplier (409 bets_closed)
GET|POST /games/api/climb/bet                  → {user, amount, height} → debits; paid the height's multiplier if the soul reaches it (up to 5 bets each)
GET|POST /games/api/blackjack/bet              → {user, amount, seat?} → takes a seat (or the queue) and debits; also /rebet /leave
GET|POST /games/api/blackjack/action           → {user, action: hit|stand|double|split|insurance|surrender} (also /hit /stand /double /split)
GET  /games/api/blackjack/seats                → who sits where, their cards, chips and the queue
```

**Games (Trivia)** — bet before each question, answer, ride or cash out:

```
GET|POST /games/api/trivia/start               → {questions, difficulty, category, lore, seconds, test}
GET|POST /games/api/trivia/bet                 → {user, amount} → a stake on the NEXT question (debit)
GET|POST /games/api/trivia/answer              → {user, answer: A-E | 1-5 | text} (anyone; only bets are paid)
GET|POST /games/api/trivia/ride · cashout      → {user} → let a win ride · take it (credit)
GET|POST /games/api/trivia/next                → end the current phase now
GET|POST /games/api/trivia/lore · lore/remove  → your own questions: list / add {question, correct, incorrect[], difficulty} / delete
GET  /games/api/trivia/bank · categories · POST asked/clear   → question pool · OpenTDB categories · a new night
GET  /games/api/trivia/table · user/{name} · ledger?since=0 · history · bets
GET|POST /games/api/trivia/stop                → end it: stakes refunded, winnings cashed out
GET|POST /games/api/trivia/preview             → {overrides, seconds} → Test in OBS: that look on screen for a few seconds (never the game)
```

`start` with `"test": true` plays either game without touching the ledger.
Rules, every bet, and the step-by-step guide for hooking up Hex's hexbank:
[docs/craps.md](docs/craps.md#hooking-up-the-bank-hexcoins) — or, when Hex does
the math: [Hex does the math (Mode B)](docs/craps.md#hex-does-the-math-mode-b).

```bash
curl "http://localhost:4747/games/api/craps/bet?user=alice&bet=pass&amount=100"
curl "http://localhost:4747/games/api/craps/roll?user=alice&wait=true"
curl "http://localhost:4747/games/api/craps/ledger?since=0"
```

**Ticker** (`/ticker/api/…`, GET with query parameters or POST with JSON; the full list is in [docs/ticker.md](docs/ticker.md#api)):

```
GET|POST /ticker/api/say?text=...&ttl=60             → one-off line, scrolls next, gone after ttl s
GET|POST /ticker/api/feed/{name}/add?text=...        → add a line (feed created if new)
GET|POST /ticker/api/feed/{name}/set?key=alice&value=1300 → keyed upsert: same key updates in place
POST     /ticker/api/feed/{name}  {"values":{...}} | {"items":[...]} + label/template/sort/limit/source
GET|POST /ticker/api/feed/{name}/remove?key=… · clear · enable?on=0 · refresh · delete
GET      /ticker/api/items · feeds · status   ·   GET|POST /ticker/api/show · hide · config
```

### Supported media formats

- **Audio:** `.mp3`, `.wav`, `.ogg`, `.m4a`, `.flac`, `.opus`
- **Video — static images:** `.png`, `.jpg`, `.jpeg` (shown for a fixed duration)
- **Video — animated images:** `.gif`, `.webp`, `.apng` (auto-converted to `.mp4`)
- **Video — native:** `.mp4`, `.webm`, `.mov`, `.mkv`

For best compatibility, transcode unfamiliar video to H.264 + AAC:
```bash
ffmpeg -i input.whatever -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -movflags +faststart output.mp4
```

### Configuration

**Media location.** Media lives in `media/` by default; set `HEXCAST_MEDIA_DIR`
to store it elsewhere (a drive, NAS mount, shared folder) — `audio/` and
`video/` are created inside it. Upgrading from old `sounds/`/`gifs/`/`videos/`
folders migrates automatically on first run.

```bash
export HEXCAST_MEDIA_DIR="/mnt/storage/hexcast"   # Windows: set HEXCAST_MEDIA_DIR=D:\hexcast-media
```

**Other settings** — constants at the top of `hexcast.py` (`PORT`, `CANVAS_W/H`,
`DEFAULT_X/Y`, `DEFAULT_SCALE`) and the bind host near the bottom:

```python
uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
#                       ^^^^^^^^^ change to "127.0.0.1" for local-only (no LAN access)
```

The overlay is canvas-agnostic (percentage-based), so the same setup works at
1080p, 1440p, 4K, or vertical.

### File layout

```
hexcast/
├── hexcast.py                 # the server: the soundboard + the plugin host (the core)
├── hexcast_core/              # the plugin system (manifest, installer, store API)
├── static/                    # the core's web files: control panel, top bar, the + store page, help
├── catalog/                   # every plugin that ships with Hexcast — what the + tab installs from
│   ├── twitch/  music/  avatar/  discord/  clips/  countdown/  ticker/  ytdlp/
│   └── games/  games_roulette/  games_craps/  games_russian/  games_trivia/  games_hexfall/  games_climb/  games_blackjack/
│                              #   each: plugin.json, its Python, static/ (panel + overlay), help.html, requirements.txt
├── plugins/                   # the plugins you installed (a copy of their catalog folder; gitignored)
├── docs/                      # plugins.md + one doc per plugin: twitch, music, avatar, discord, clips, countdown, games, craps, ticker …
├── tools/                     # build_catalog.py (make a remote plugin catalog)
├── tests/                     # pytest suite for the plugin system
├── config/                    # settings, tokens, playlists, games tables + ledger (gitignored)
├── requirements.txt           # the core's packages only — each plugin brings its own
├── start.sh / start.bat       # launchers
└── media/                     # auto-created: audio/ and video/ (plus overlays/ and avatars/ when those are used)
```

Each clip may have adjacent files: `airhorn.mp4` (media), `airhorn.json` (saved
settings, non-default values only), `airhorn.poster.jpg` (auto thumbnail). You
can hand-edit the JSON; the watcher ignores `.json` writes.

### Troubleshooting

- **Check `hexcast.log` first** (next to `hexcast.py`, rotated at 1 MB × 3). It records conversion/poster failures with ffmpeg's real error output, and playback errors reported back from inside the OBS browser source.
- **No posters / gifs animate in the picker:** ffmpeg isn't in PATH — run `ffmpeg -version`.
- **Clip fires but nothing shows, log says "decode failed":** the file is corrupt — re-upload (a race in versions ≤1.1.0 could corrupt converted gifs; fixed since).
- **No audio in OBS:** enable **Control audio via OBS** on the browser source; it appears in the Audio Mixer.
- **Browser source stays black:** right-click → **Interact** → check the DevTools console; confirm Width/Height match the canvas and you've fit with Ctrl+F.
- **Overlay CSS changes don't show:** OBS caches hard — **Interact → Ctrl+Shift+R**, or append `?v=N` to the URL.
- **"unsupported extension" / black-in-OBS video:** convert to H.264 + AAC MP4 as shown above.
- **SSL error in the browser:** you typed `https://`; the server only speaks `http://`.
- **Music video doesn't show / song pauses at track changes:** see the Music doc's [troubleshooting](docs/music.md#troubleshooting) — the Music panel's *Video:* line gives the reason, and the short pause is the (optional) hold that starts song and video together.

---

## Links

- **Repository & downloads:** <https://github.com/UMDSmith/hexcast>
- **Plugins:** [how plugins work & how to write one](docs/plugins.md)
- **Plugin docs:** [Twitch](docs/twitch.md) · [Music](docs/music.md) · [Discord](docs/discord.md) · [Clips](docs/clips.md) · [Countdown](docs/countdown.md) · [Games & Roulette](docs/games.md) · [Craps](docs/craps.md) · [Russian Roulette](docs/russian_roulette.md) · [Trivia](docs/trivia.md) · [Hexfall](docs/hexfall.md) · [Soul Climb](docs/climb.md) · [Blackjack](docs/blackjack.md) · [Ticker](docs/ticker.md)
- **License:** MIT — see [LICENSE](LICENSE)

<p align="center">
  <img src="assets/hexcast.png" width="96" alt="Hexcast">
</p>
