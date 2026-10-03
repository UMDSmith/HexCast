# Clips — Twitch clip player with a queue

A clip player for stream: queue up Twitch clip and VOD links, then fire them
one at a time at a full-window OBS browser source. **Nothing auto-advances** —
every item is played deliberately, from the panel or from a bot over a simple
GET API, the same philosophy as the soundboard's `/api/play/{name}`.

Everything lives under `/clips/*`.

---

## Why yt-dlp

The module plays **direct media, not the Twitch iframe**: the overlay gets a
real `<video>` element it can pause, seek, report progress on, and clear the
instant it ends. Twitch doesn't hand out MP4/HLS URLs in the page link, so
something has to resolve them — that something is `yt-dlp`, which despite the
name is the standard resolver for Twitch clips and VODs too (nothing here
touches YouTube). It's in the main `requirements.txt`; if you already have
`yt-dlp` on your PATH, that copy is used automatically and the pip install is
unnecessary.

The Twitch iframe embed is kept only as a best-effort **fallback** for things
direct playback can't reach (sub-only VODs, expired clips).

---

## Install

Open the **+** tab in the top bar, find **Clips** and press **Install**. The tab appears straight away, and the
Python packages it needs are installed for you - no restart. From a terminal:
`python hexcast.py plugins install clips`. (More in [Plugins](plugins.md).)

Clips resolves links with yt-dlp, which lives in a small shared plugin (**yt-dlp helpers**) that is installed along
with it - the Music plugin uses the same one. Two URLs are printed at startup. As with the other plugins the module
starts lazily on the first request; call `start_clips()` from inside `hexcast.py`'s lifespan to start at boot instead.

---

## Browser source

| Source | URL |
| --- | --- |
| Clip player | `http://localhost:4747/clips/overlay` |

Add it as a Browser source sized and placed where clips should appear — the
video letterboxes to fit (`object-fit: contain`), so covering the whole canvas
is fine: the overlay renders **nothing at all** while idle. Stopped or ended =
fully transparent.

Uncheck **Shutdown source when not visible**. In the browser source's audio
settings, "Control audio via OBS" works normally — media elements are attached
to the DOM specifically so OBS captures their audio, the same trick the
soundboard overlay uses.

---

## Queue workflow

1. **Add** — paste into the box on the Queue tab: a single URL, or any blob of
   text (a chat log, a Discord dump). Every Twitch link in it is extracted:

   - `clips.twitch.tv/SLUG`
   - `twitch.tv/CHANNEL/clip/SLUG`
   - `twitch.tv/videos/ID` — an optional `?t=1h2m3s` start offset is honoured

   Duplicates are skipped. Each new item gets a stable **#number** that never
   changes and never gets reused — that's what bots reference. Because numbers
   are never reused, the counter only ever climbs; the **Reset numbering**
   button renumbers the current queue 1..N and restarts the counter (any old
   numbers a bot still references stop working, so do it between streams).

2. **Resolve** — metadata (title, duration, thumbnail) fills in a few seconds
   after adding, in the background. With **Pre-download clips** on (the
   default), each clip's MP4 is also downloaded to `media/clips/{id}.mp4`, so
   playback is instant and immune to Twitch's short-lived media URLs. VODs are
   never downloaded — they stream as HLS.

3. **Play** — hit Play on a row (or `GET /clips/api/play/{num}`). The Now
   Playing strip shows title, progress, Pause/Resume and Stop. When the clip
   ends the overlay clears itself and the item is marked **played** — dimmed
   in the list, but kept until you **Clear played**.

   - **Stop** clears the overlay immediately; the item stays queued and is
     *not* marked played.
   - Drag rows to reorder; the per-row buttons toggle played state and remove
     items (removal also deletes the cached MP4).
   - **Rename** on a row lets you give a clip your own title (Enter saves, Esc
     cancels). It shows in the list, the Now Playing strip and — with the clip
     title line on — the overlay's credit, live if that clip is playing. The
     original title is kept: a **renamed** badge marks the row, **Original** (or
     saving an empty title) restores it, and a re-resolve never overwrites your
     title.

Playback state is owned by the server and pushed to every open panel and
overlay over websockets, so multiple panels stay in sync and a reloaded OBS
source rejoins mid-clip at the right position.

---

## Fallback behaviour

If resolution fails — sub-only VOD, deleted clip, network trouble — the item
gets an error badge and, if **Fall back to the Twitch embed** is on, playback
retries through the official iframe embed (`clips.twitch.tv/embed` /
`player.twitch.tv`). Two caveats, both inherent to the iframe:

- It cannot report progress or "ended". When the duration is known, the
  server clears the overlay itself a few seconds after the clip should have
  finished; otherwise press Stop.
- Twitch requires a `parent` hostname it accepts. The overlay passes the
  hostname it was loaded from — `localhost` works; a bare LAN IP may not.
  Pause/Resume don't work in this mode.

---

## Channel credit

The overlay can draw an attribution label over the playing clip showing where
it came from — `twitch.tv/channelname` for Twitch clips and VODs (the
broadcaster, not whoever made the clip), `youtube.com/@handle` for YouTube,
the uploader or site name for generic media. The channel is pulled from
yt-dlp's metadata when the item resolves; shoutout clips know their channel
immediately.

Configure it on the Settings tab: an on/off toggle, an optional clip-title
line, font family (Google Fonts names load automatically), size, color and a
drop shadow. Placement works like the soundboard's edit mode — drag the label
around a 16:9 preview canvas (center-anchored `x`/`y` percentages) or use the
3×3 quick-position pad. Everything lives under `settings.credit` in
`config/clips.json` and applies to the live overlay the moment you save.

---

## Volume leveling

Clips (and `!so` shoutouts, which play through this same overlay) can arrive at
wildly different volumes. Turn on **Auto-level clip volume** in the Settings tab
and Hexcast evens them out.

How it works: when a clip resolves, the server measures its integrated loudness
(EBU R128 / LUFS) with ffmpeg — streamed *through* ffmpeg, **nothing is
downloaded or saved** — then the overlay attenuates louder clips down to your
**Target loudness** (default `-16` LUFS). It normalises *toward* the target:
clips louder than it are turned down to match; clips already quieter are left
alone (browser audio can only be turned *down* for streamed media, not boosted).

Worth knowing:

- Needs **ffmpeg**. Without it, leveling is skipped and the master volume applies.
- A clip that starts playing before its measurement finishes — e.g. an instant
  shoutout — plays at full volume for a moment, then corrects itself once the
  measurement lands.
- **Iframe-embed** fallbacks can't be measured or adjusted (the audio lives in a
  cross-origin frame), so those use the master volume only. Very long VODs are
  skipped too.
- Independent of pre-download — it works on streamed clips.

---

## YouTube sign-in (optional)

Clips are looked up anonymously. For YouTube videos that won't look up
anonymously, the Settings tab has a **YouTube sign-in** card with **Link** and
**Unlink** buttons. This is separate from the Music page's sign-in; linking one
doesn't link the other.

**What Link does, exactly**

- Nothing happens until you click **Link**. The default is *not linked*, and
  every lookup is anonymous.
- Link first runs a test: yt-dlp reads the chosen browser's cookies, checks
  there's a YouTube sign-in among them, and does one test lookup. It tells
  you what it found. If the cookies can't be read or there's no YouTube
  sign-in, nothing is saved.
- What's saved is only the **browser's name** (`"firefox"` or `"chrome"`),
  in `config/clips.json` (`settings.cookies_browser`). Your cookies are never copied, stored or logged by Hexcast.
- Lookups stay **anonymous first**. Only when YouTube refuses one for a reason
  a sign-in can fix (age-restricted, members-only, "Sign in to confirm you're
  not a bot") does Hexcast re-run yt-dlp with `--cookies-from-browser <name>`.
  yt-dlp then reads that browser's cookies from its profile on the PC running
  Hexcast, at that moment, and sends them to YouTube as part of that one
  lookup. That lookup is done **as your YouTube account**.
- The link **stays across restarts** until you click **Unlink**, which takes
  effect immediately.
- Chrome locks its cookie database while it's running, so the Chrome option
  only works with Chrome fully closed. Firefox works while open.
- Signed-in lookups make yt-dlp solve a JavaScript challenge from YouTube,
  which needs two things on the PC running Hexcast:
  - **a JavaScript runtime** — [Deno](https://deno.com) is what yt-dlp prefers
    (`winget install DenoLand.Deno` on Windows, then restart Hexcast). If
    there's no Deno but Node or Bun is installed, Hexcast uses that instead.
  - **yt-dlp's challenge-solver scripts** — the `yt-dlp-ejs` package, installed
    by `requirements.txt`. **Update yt-dlp** (Clips page) upgrades it together
    with yt-dlp, since the two have to match.

  The sign-in card shows a *Requirements* line with both, and if the Link test
  fails it names the missing piece.

**How to link**

1. Sign in to YouTube in Firefox — or in Chrome, then close Chrome completely.
2. Check the card's *Requirements* line shows both pieces ready.
3. Pick the browser and click **Link**. Read the message: it says whether the
   test lookup worked, or what's missing.

Hexcast is built for a home/studio network. Anything that can reach its port
can use these features, so **never expose Hexcast to the internet** (see
[Security](../README.md#️-security-local-network-use-only)). Linking is your
choice and your responsibility.

## Bot / HTTP API

All endpoints are GET-friendly and return `{"ok": true, ...}` or
`{"ok": false, "error": "..."}`. `{ref}` is a **#number**, an item id, an
exact clip slug, or `next` (the first still-queued item).

| Endpoint | What it does |
| --- | --- |
| `GET /clips/api/queue` | full queue + player state as JSON |
| `GET /clips/api/add?url=...` | add a URL (returns the created entry incl. its `num`); POST a JSON `{"text": "..."}` blob to add many at once |
| `GET /clips/api/play/{ref}` | play an item |
| `GET /clips/api/pause` · `resume` · `toggle` · `stop` | transport |
| `GET /clips/api/status` | player state, current item, queue counts |
| `GET /clips/api/remove/{ref}` | remove an item (`DELETE /clips/api/queue/{ref}` also works) |
| `POST /clips/api/title` | body `{"ref": "7", "title": "My title"}` — rename an item (up to 200 characters); an empty title restores the original. The response is the updated entry, whose `title` is the one shown and `original_title` the one yt-dlp found |
| `POST /clips/api/reset_numbers` | renumber the queue 1..N and restart the counter |
| `POST /clips/api/update_ytdlp` | upgrade yt-dlp in place (pip for the bundled module, `-U` for a standalone binary) |
| `POST /clips/api/login/link` | body `{"browser": "firefox"}` or `"chrome"` — test the browser's YouTube sign-in, then link it if the test passes (see [YouTube sign-in](#youtube-sign-in-optional)) |
| `POST /clips/api/login/unlink` | unlink (back to anonymous) |
| `GET /clips/api/shoutout/{channel}?count=2` | play random clips from that Twitch channel back to back, ephemerally (nothing queued or saved) — this is what the Twitch module's `!so` command uses |

Examples:

```
curl "http://localhost:4747/clips/api/add?url=https://clips.twitch.tv/SomeSlug"
curl http://localhost:4747/clips/api/play/7
curl http://localhost:4747/clips/api/play/next
curl http://localhost:4747/clips/api/stop
```

So a channel-point redeem or a `!playclip 7` chat command is one HTTP call.

---

## Storage

Everything persists in `config/clips.json` — settings, the number counter,
and the queue itself (each entry: id, num, url, kind `clip|vod`, title (yt-dlp's)
and `custom_title` (your rename, if any), channel + credit (the attribution label), duration, thumbnail, status
`queued|played`, start offset, error, source `manual|api`). Cached clip MP4s live in `media/clips/` and are served through
the existing `/media` mount; they're deleted when their queue item is removed
or cleared.

No tokens, no secrets — the module talks to Twitch anonymously through
yt-dlp. Same security posture as the rest of Hexcast otherwise: no auth, keep
it on the LAN.

---

## Troubleshooting

**Status pill says "yt-dlp missing".** Install it into Hexcast's environment
(`pip install yt-dlp`) or put the standalone `yt-dlp` binary on PATH, then
reload the panel.

**A clip resolves but won't pre-download.** It streams at play time instead —
pre-download is an optimisation, not a requirement. Check the Log card on the
Settings tab for the reason.

**Sub-only VODs.** Anonymous yt-dlp can't fetch them, so they fall back to
the iframe embed — which also only works if the OBS browser source is logged
out-of-scope. Expect these to need the fallback, or to fail entirely.

**Clip plays but there's no audio in the stream.** Check the browser source's
audio routing in OBS (Advanced Audio Properties), and the master volume on
the panel's Settings tab.

**YouTube items suddenly error (but Twitch works).** YouTube changes
constantly and old yt-dlp builds stop working — hit **Update yt-dlp** on the
Settings tab first. If an up-to-date yt-dlp is still refused ("Sign in to
confirm you're not a bot", age-restricted or members-only videos), you can
**Link** your YouTube sign-in on the same tab — see
[YouTube sign-in](#youtube-sign-in-optional).

**Old clips error with "no longer available".** Twitch deletes clips; the
error badge shows exactly what yt-dlp reported. Remove the row.

**The overlay shows a Twitch player frame instead of clean video.** That's
the iframe fallback kicking in — the row will carry an error badge explaining
why direct playback failed.
