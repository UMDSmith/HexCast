# Games — bot-driven overlay games (Roulette)

A home for games your chat — or a bot — can play on stream. The first one is
**Roulette**: trigger a spin from the panel or with one HTTP call and a casino
wheel pops onto the stream. The wheel spins, the ball is launched the other
way, slows, drops off the track, bounces off the deflectors and frets, and
settles in a pocket; then the result pops up. Bets are optional: send them
with the spin and every one comes back resolved at standard roulette odds,
ready for your bot to pay out.

The **server** picks the pocket; the overlay only animates it. Everything
lives under `/games/*`.

---

## Fairness

- The pocket is chosen on the server with Python's `secrets.SystemRandom()`
  (the operating system's cryptographic random source), uniformly over all 38
  pockets of the wheel. Every pocket has exactly the same chance — 1 in 38 —
  on every spin, whatever came before.
- The overlay and the panel only **animate** the server's result. There is no
  parameter, config key or panel button that picks or nudges the outcome — not
  bets, not test spins, not per-spin overrides.
- Each spin carries a `seed`, but it only varies the *animation* (how the ball
  bounces on the way in), never the outcome.
- The **hot** and **cold** numbers in the stats are history, not prediction.

---

## The wheel

A standard **American double-zero** wheel: 38 pockets — `0`, `00` and `1`–`36`.
Red numbers are `1 3 5 7 9 12 14 16 18 19 21 23 25 27 30 32 34 36`; the other
numbers from 1 to 36 are black; `0` and `00` are green. Pocket order,
clockwise starting at zero:

```
0 28 9 26 30 11 7 20 32 17 5 22 34 15 3 24 36 13 1 00 27 10 25 29 12 8 19 31 18 6 21 33 16 4 23 35 14 2
```

Every bet pays standard odds, so the house edge is the usual double-zero one:
**5.26%** on every bet (2 units in 38), except the five-number **basket** at
**7.89%** (3 in 38). Even-money bets (red/black, odd/even, low/high) simply
lose on `0` and `00` — there's no *la partage* half-back rule.

Every result and spin object carries `"wheel": "american"` — informational,
there's nothing to configure.

---

## Install

Copy `games.py` next to `hexcast.py`, and these files into `static/`:

```
static/games_panel.html
static/games_overlay.html
static/games/roulette.js
```

`hexcast.py` already mounts the module when the file is present — like Clips
it's optional, so if `games.py` is missing it's skipped quietly. If you're
wiring it into your own copy by hand, it's the same two lines as the other
integrations, after `app.mount("/media", ...)`:

```python
from games import attach_games
attach_games(app, PORT)
```

Restart Hexcast; new routes only appear after a restart. There are no extra
dependencies.

---

## Browser source

| Source | URL |
| --- | --- |
| Games | `http://localhost:4747/games/overlay` |
| Roulette only | `http://localhost:4747/games/overlay?game=roulette` |

Add it as a **1920×1080** Browser source (match your canvas) — the page is
transparent and the wheel sits on a 1920×1080 stage that scales to whatever
size you give the source, so everything keeps its proportions. Roulette is the
only game today, so the plain URL is fine; `?game=` exists so later games can
each get their own source.

- Uncheck **Shutdown source when not visible** (otherwise the websocket dies)
  and **Refresh browser when scene becomes active**.
- Turn **Control audio via OBS** on. The built-in ball sounds — the rolling
  rumble, fret clicks and the landing clack — are synthesized inside the
  overlay, so this is how they reach your mixer.
- **Launch/landing clips** (see [Soundboard clip cues](#soundboard-clip-cues))
  play on the soundboard overlay (`/overlay`), not this one — keep both
  browser sources in the scene.

The **OBS browser source** card on the panel shows the exact URL for the
machine you're on, with a **Copy** button.

---

## What viewers see

1. The wheel **pops in** (a quick scale-and-fade) at its saved placement. If
   the spin has a `user` and **show user** is on, an `@user spins` caption
   appears.
2. The wheel spins **clockwise** and the ball is launched
   **counter-clockwise** around the outer track. The ball slows, leaves the
   track a little past half-way through the spin, spirals inward, takes a few
   damped bounces off the diamond deflectors and the pocket frets, and settles
   in the winning pocket exactly when the spin time runs out. The ball then
   rides with the wheel while it coasts.
3. The winning pocket glows and the **result badge** pops in — the number on
   a disc in the pocket's colour, with a details line like `ODD · LOW · 2ND 12`
   underneath (when **result details** is on). The badge sits over the centre
   turret, or above/below the wheel (**result position**).
4. If the spin carried bets and **show bets** is on, a **winners list**
   appears (up to **bets max** entries).
5. The **history strip** under the wheel (last **history count** results)
   updates only *after* the ball lands — it never gives away the current spin.
6. After **result seconds** the wheel fades out (with **hide when idle** on);
   with it off, the wheel stays on screen, idling.

Every overlay and the panel's live preview render the **identical** spin: the
animation is seeded, and the server tells each one how far into the spin it
is. A source that (re)connects mid-spin jumps straight to the right moment; one
that connects during the result shows the landed result.

**Themes:** `classic` (dark walnut rim, gold frets and turret, casino red /
near-black / green pockets), `neon` (black rim, Hexcast-red glow, cyan and
white frets), `midnight` (navy and steel, silver frets), `royal` (deep purple
and gold). Each pocket colour can be overridden with your own.

---

## The panel

Everything is on the panel at `/games`. The top bar carries **↗ Overlay**
(opens the browser source in a new tab), **⏹ Stop** (abort and hide
everything — see [Show, hide, stop](#show-hide-stop)) and the **Edit Mode**
toggle, which turns amber when on, exactly like the soundboard's.

- **OBS browser source** — the overlay URL for this host, with **Copy**.
- **Roulette** — a live mirror: a 16:9 preview holding a scaled 1920×1080
  stage with the real wheel (sound off), playing the same spins as the
  overlay. **Spin** starts a spin, **Show** / **Hide** put the idle wheel on
  or off screen. A readout shows the state (`idle`, `spinning`, `result`,
  `cooldown`) with a countdown, plus a chip with the last result.
- **History & stats** — the last 20 results as coloured chips (newest
  first), red/black/green, odd/even and low/high counts, hot and cold numbers,
  the current streak, and **Clear history**.
- **Settings** — spin seconds, result seconds, hide when idle, cooldown,
  show user caption, show bets, bets max, the soundboard clips for launch and
  landing (suggestions come from your soundboard library), and built-in sounds
  with their volume. **Save settings** stores them.
- **Bet tester** — type a bet string (and an amount) to see how it parses:
  type, odds and covered numbers, or the error. **Spin with this bet** sends
  it along with a real spin.
- **API** — a compact endpoint table with curl examples built for this host.

### Edit Mode — placement and appearance

Placement works exactly like the soundboard's edit mode. Turn on **Edit
Mode**, then click the Roulette card (or its **✎ Edit placement** button) to
open the editor:

- A 16:9 preview of the 1920×1080 stage with the real wheel at its working
  position. **Drag the wheel** to move it; the **Position** readout shows
  `x:50% y:50%` (the wheel's centre, as a percentage of the canvas).
- **Scale** slider from `0.2` to `5` (`1.25x` readout). At scale 1 the wheel
  is 400 px across on the 1920×1080 canvas, so the default `1.25` is 500 px.
- The 3×3 **quick positions** pad snaps to the corners, edges and centre
  (15 / 50 / 85 %).
- **Appearance**, live in the preview: theme, the three pocket colours (each
  with a *theme default* reset), result position (center / below / above),
  result details, show result, show history, and history count.

The buttons along the bottom:

- **▶ Preview** — a local demo spin inside the editor (random pocket, your
  configured spin time). Nothing is sent to the server or the stream.
- **Test in OBS** — a real `test` spin on the overlay using the editor's
  *unsaved* placement and appearance, so you can see it on stream before
  committing. Test spins stay out of history and stats and don't fire clip
  cues. If the wheel is busy you'll get a toast instead.
- **Reset** — back to the defaults: `x` 50, `y` 50, scale 1.25, and the
  default look (the `classic` theme with its own pocket colours). Not saved
  until you press Save.
- **Cancel** / **×** / **Esc** / clicking the backdrop closes without saving;
  **Save** stores placement and appearance and restyles every overlay live.

---

## Soundboard clip cues

In **Settings**, **Launch clip** names a soundboard clip to fire the moment
the ball is thrown (`spin_clip`) and **Landing clip** one to fire when it
lands (`land_clip`). Leave either empty for none.

- Clips fire through the soundboard's own `/api/play/{name}`, so each clip's
  saved position, scale, volume, trim and cooldown all apply, and names match
  like the Bot API (case-insensitive, audio checked before video).
- They play on the **soundboard overlay** (`/overlay`), not the games overlay —
  both browser sources need to be in the scene.
- **Test** spins never fire cues.
- The built-in ball sounds are separate: they're synthesized in the games
  overlay and controlled by **Built-in ball sounds** and **Built-in sound
  volume** (`sfx`, `sfx_volume`).

---

## Timeline of a spin

```
launch ──── spin seconds ────► landing ──── result seconds ────► result ends ── cooldown ──► ready
  │                              │                                │
  spin_clip fires                land_clip fires                  wheel hides
                                 spin committed to history        (if hide when idle)
```

- At **launch** the server has already picked the pocket; the API response
  (unless `wait=true`) comes back immediately with the result in it.
- At **landing** the spin is committed to history and stats (unless it's a
  test), `land_clip` fires, and the overlay shows the result.
- **Result seconds** later the result phase ends — the wheel hides if **hide
  when idle** is on — then the optional **cooldown** runs.
- While any of that is going on (`spinning`, `result` or `cooldown`) new spins
  are refused with **409 busy**. With the defaults (9 s spin, 6 s result, no
  cooldown) that's one spin every 15 seconds at most.

---

## Bets

A bet is an object:

```json
{"user": "bob", "bet": "split:17/20", "amount": 50}
```

`user` and `amount` are optional. Bet strings are case-insensitive and spaces
are ignored. Bare number lists (like `17/20` or `13,14,15`) can use `/` or `,`
as the separator, and the order doesn't matter.

### Bet reference

Odds are net, "X to 1": a winning 100 on a split pays 1,700 plus the 100
stake back.

| Type | Syntax | Covers | Pays |
| --- | --- | --- | --- |
| `straight` | `17`, `0`, `00` | one number | 35 to 1 |
| `split` | `split:17-20`, `split:17/20`, or bare `17/20` | two numbers next to each other on the table, side by side or one above the other | 17 to 1 |
| — zero splits | `0/1`, `0/2`, `00/2`, `00/3`, `0/00` | `0` or `00` and a number beside it, or `0` and `00` | 17 to 1 |
| `street` | `street:13` (first number of the row: 1, 4, … 34), `street:13-14-15`, or bare `13/14/15` | a row of three | 11 to 1 |
| `trio` | `0/1/2`, `0/00/2`, `00/2/3` (a `trio:` prefix is also accepted) | three numbers including `0` and/or `00` | 11 to 1 |
| `corner` | `corner:17` (the lowest number, top-left of the block — not in column 3 or the last row), `corner:17-18-20-21`, or four bare numbers forming a 2×2 block | four numbers | 8 to 1 |
| `basket` | `basket`, `topline`, `five`, `0/00/1/2/3` | 0, 00, 1, 2, 3 | 6 to 1 |
| `six_line` | `line:13` (first number of the upper row: 1, 4, … 31), `line:13-18`, `sixline:13`, or six bare numbers from two neighbouring rows | two rows (six numbers) | 5 to 1 |
| `dozen` | `dozen1` `d1` `1st12` `1-12` · `dozen2` `d2` `2nd12` `13-24` · `dozen3` `d3` `3rd12` `25-36` | 1–12 · 13–24 · 25–36 | 2 to 1 |
| `column` | `col1` `column1` `c1` · `col2` · `col3` | 1, 4, 7 … 34 · 2, 5 … 35 · 3, 6 … 36 | 2 to 1 |
| `red` / `black` | `red` `r` · `black` `b` | 18 numbers | 1 to 1 |
| `odd` / `even` | `odd` · `even` | 18 numbers | 1 to 1 |
| `low` / `high` | `low` `1-18` `manque` · `high` `19-36` `passe` | 1–18 · 19–36 | 1 to 1 |

There's no "first four" bet on a double-zero wheel — `firstfour`, `first4` and
`0/1/2/3` come back invalid. The top-line bet here is the **basket**,
`0/00/1/2/3`.

"On the table" means the standard betting layout: 1–36 in twelve rows of
three (`1 2 3`, `4 5 6`, … `34 35 36`), so column 1 is 1, 4, 7 … 34. `17/20`
is a split (one above the other), `17/18` is a split (side by side), `18/19`
is not (18 ends one row, 19 starts the next).

Test any string with the panel's **Bet tester** or
`GET /games/api/roulette/validate?bet=...` before you trust it — see
[Validate](#validate-and-the-bet-reference).

### How bets are resolved

Every bet you send comes back with extra fields:

| Field | Meaning |
| --- | --- |
| `valid` | `false` if the string didn't parse, isn't a bet on this wheel, or the `amount` isn't a number ≥ 0 |
| `error` | why it's invalid (only on invalid bets) |
| `type` | one of the types in the table above (`invalid` when the bet string itself didn't parse) — check `valid` first |
| `label` | a readable name for the bet, for chat messages |
| `numbers` | the pocket labels it covers, as strings (`"00"` stays `"00"`) |
| `odds` | net odds (`35` for a straight, `1` for red …) |
| `win` | whether it won |
| `payout` | net change: a win is `amount × odds`, a loss is `-amount` |
| `returned` | what goes back to the player: a win is `amount × (odds + 1)`, a loss is `0` |

- No `amount` counts as `0` — the bet still reports win or lose, with a
  payout of `0`. Handy for "guess the number" games with no stakes.
- An **invalid bet never blocks the spin**. It comes back with
  `valid: false`, the `error`, `win: false`, `payout: 0` and
  `returned: amount` — the stake refunded.
- Hexcast keeps **no balances or points**. If your bot takes the stake when
  the bet is placed, crediting every bet's `returned` after the spin is all it
  needs: winners get stake + winnings, invalid bets get a refund, losers get
  nothing.

---

## HTTP API

All endpoints return JSON with `"ok": true` or `"ok": false` (plus an
`"error"`). `{game}` is `roulette` — the only game so far; any other name gets
a 404 `{"ok": false, "error": "unknown game"}`. No authentication (LAN tool,
like the rest of Hexcast).

| Endpoint | What it does |
| --- | --- |
| `GET /games` | the panel |
| `GET /games/overlay` | the OBS browser source (optional `?game=roulette`) |
| `GET /games/api` | endpoint index as JSON |
| `GET /games/api/status` | overlay count + every game's state |
| `GET /games/api/config` | full config as JSON |
| `POST /games/api/config` | merge-and-save any subset of config keys |
| `GET\|POST /games/api/{game}/spin` | start a spin (alias `/games/api/{game}/play`) |
| `GET /games/api/{game}/last` | the last committed spin and its result |
| `GET /games/api/{game}/history?limit=20` | recent spins (newest first) + stats |
| `POST /games/api/{game}/history/clear` | clear history and stats (GET works too) |
| `GET /games/api/{game}/bets` | bet-type reference: types, syntax examples, odds |
| `GET /games/api/{game}/validate?bet=...` | parse one bet string without spinning |
| `GET\|POST /games/api/{game}/show` | put the idle wheel on screen |
| `GET\|POST /games/api/{game}/hide` | take it off screen |
| `GET\|POST /games/api/stop` | abort every animation and hide everything |
| `GET\|POST /games/api/{game}/stop` | the same, for one game only |
| `WS /games/ws/overlay`, `WS /games/ws/panel` | live updates (see [WebSocket protocol](#websocket-protocol)) |

### Spin

`GET` or `POST /games/api/roulette/spin` (or `/play`). Parameters go in the
query string, a JSON body (POST), or both — the body wins where they overlap.

| Parameter | What it does |
| --- | --- |
| `user` | who's spinning, up to 40 characters — shown as an `@user spins` caption |
| `duration` | spin time in seconds, clamped to 4–30 (default: **spin seconds** from the config) |
| `wait` | `true` holds the HTTP response until the ball has landed (default `false`) |
| `test` | `true` for a test spin: kept out of history and stats, no clip cues |
| `bets` | JSON body: a list of up to 200 bets, `[{"user": ..., "bet": ..., "amount": ...}]` |
| `bet`, `amount` | query shorthand for a single bet; the `user` param becomes the bet's user |
| `overrides` | JSON body: appearance keys for this spin only (see [Config keys](#config-keys)) |
| `x`, `y`, `scale` | query shorthand for per-spin placement, like the soundboard's per-play overrides |

Only the appearance keys can go in `overrides`; result seconds, clip cues and
cooldown always come from the config (the spin time has its own per-spin
parameter, `duration`). Overrides are validated like the config and apply to
that one spin — the overlay goes back to its saved placement and look
afterwards.

Examples:

```
curl http://localhost:4747/games/api/roulette/spin
curl "http://localhost:4747/games/api/roulette/spin?user=bob&bet=red&amount=100"
curl "http://localhost:4747/games/api/roulette/spin?user=bob&bet=17&amount=10&wait=true"
curl "http://localhost:4747/games/api/roulette/spin?x=80&y=25&scale=0.8"
curl -X POST http://localhost:4747/games/api/roulette/spin -H "Content-Type: application/json" -d "{\"test\":true}"
```

A spin with several bets and an appearance override:

```json
POST /games/api/roulette/spin
{
  "user": "hexmod",
  "wait": true,
  "bets": [
    {"user": "alice", "bet": "split:17/20", "amount": 50},
    {"user": "bob",   "bet": "corner:19",   "amount": 100}
  ],
  "overrides": {"theme": "neon", "result_position": "below"}
}
```

```
curl -X POST http://localhost:4747/games/api/roulette/spin -H "Content-Type: application/json" -d "{\"user\":\"hexmod\",\"wait\":true,\"bets\":[{\"user\":\"alice\",\"bet\":\"split:17/20\",\"amount\":50},{\"user\":\"bob\",\"bet\":\"corner:19\",\"amount\":100}],\"overrides\":{\"theme\":\"neon\",\"result_position\":\"below\"}}"
```

Response (HTTP 200) — the **spin object**:

```json
{
  "ok": true,
  "id": "r-1a2b3c4d",
  "game": "roulette",
  "result": {"number": "17", "value": 17, "color": "black", "index": 9, "wheel": "american",
             "parity": "odd", "range": "low", "dozen": 2, "column": 2},
  "user": "hexmod",
  "test": false,
  "seed": 123456789,
  "wheel": "american",
  "duration_ms": 9000,
  "result_ms": 6000,
  "started_at": 1790000000.123,
  "lands_at": 1790000009.123,
  "bets": [
    {"user": "alice", "bet": "split:17/20", "amount": 50,
     "valid": true, "type": "split", "label": "Split 17/20", "numbers": ["17", "20"],
     "odds": 17, "win": true, "payout": 850, "returned": 900},
    {"user": "bob", "bet": "corner:19", "amount": 100,
     "valid": true, "type": "corner", "label": "Corner 19/20/22/23", "numbers": ["19", "20", "22", "23"],
     "odds": 8, "win": false, "payout": -100, "returned": 0}
  ],
  "summary": {"bets": 2, "winners": 1, "invalid": 0, "total_wagered": 150, "total_payout": 750},
  "overrides": {"theme": "neon", "result_position": "below"},
  "landed": true
}
```

(`label` text is for display — match on `type` and `numbers` in code.)

- `result` — the pocket. `number` is the label as a string (`"00"` never
  collides with `"0"`); `value` is the number as an integer, `-1` for `00`;
  `index` is the pocket's position in the wheel order above; `color` is
  `red`, `black` or `green`. `parity` (`odd`/`even`), `range` (`low` 1–18 /
  `high` 19–36), `dozen` (1–3) and `column` (1–3) are `null` for `0` and `00`.
- `started_at` / `lands_at` — server epoch seconds, so a bot can schedule its
  own announcement for `lands_at` instead of using `wait`.
- `duration_ms` — launch to landing; `result_ms` — how long the result stays
  up afterwards.
- `seed` — animation variety only, not the outcome.
- `summary` — how many bets, how many won, how many were invalid, and the
  total staked and total net `payout` (both counted over the valid bets only)
  — for a one-line chat recap.
- `landed: true` — only present with `wait=true`, where the response is sent
  the moment the ball lands. If the spin is stopped (`/games/api/stop`) before
  it lands, the waiting response comes back right then with
  `"landed": false, "stopped": true` — the result and bets still stand.

**Without `wait`** the response comes back instantly — it already contains the
result, so don't announce it until `lands_at` (or use `wait=true`) or you'll
spoil the spin. With `wait=true`, give your HTTP client a timeout longer than
the spin (up to 30 s).

### Busy

Only one spin at a time. While a spin is in the air, in its result phase, or
in cooldown, a new spin gets **HTTP 409**:

```json
{"ok": false, "error": "busy", "retry_in_ms": 11240, "state": "spinning"}
```

`state` is `spinning`, `result` or `cooldown`; `retry_in_ms` is how long until
a spin will be accepted. Nothing is queued — try again after `retry_in_ms`, or
tell chat to wait.

### Last spin, history & stats

```
curl http://localhost:4747/games/api/roulette/last
curl "http://localhost:4747/games/api/roulette/history?limit=5"
curl -X POST http://localhost:4747/games/api/roulette/history/clear
```

`/last` returns `{"ok": true, "result": {...}, "spin": {...}}` for the last
committed spin (both `null` before the first one). Test spins are never
committed.

`/history` returns `{"ok": true, "history": [spin, ...], "stats": {...}}`,
newest first. `stats`:

```json
{
  "spins": 42,
  "red": 20, "black": 21, "green": 1,
  "odd": 19, "even": 22,
  "low": 23, "high": 18,
  "counts": {"17": 3, "32": 2, "0": 1},
  "hot": ["17", "32", "4", "21", "19"],
  "cold": ["26", "3", "35", "12", "28"],
  "streak": {"color": "red", "length": 3}
}
```

`counts` is hits per pocket label; `hot` is the five most-hit pockets, `cold`
the five least-seen (or never-seen); `streak` is the current run of one
colour. History lives in memory only — up to 200 spins, cleared on restart or
by `/history/clear`.

### Validate and the bet reference

```
curl "http://localhost:4747/games/api/roulette/validate?bet=split:17/20"
curl "http://localhost:4747/games/api/roulette/validate?bet=basket"
```

```json
{"ok": true, "bet": "split:17/20", "valid": true, "type": "split", "label": "Split 17/20",
 "numbers": ["17", "20"], "odds": 17}
```

An invalid bet still returns `"ok": true`, with `"valid": false` and an
`"error"` explaining why. URL-encode the bet string if your bot's HTTP tool
doesn't do it for you.

`GET /games/api/roulette/bets` returns the whole bet reference — every type
with syntax examples and odds — so a bot can build its own `!bets` help text.

### Show, hide, stop

```
curl http://localhost:4747/games/api/roulette/show
curl http://localhost:4747/games/api/roulette/hide
curl http://localhost:4747/games/api/stop
```

- **show** — put the wheel on screen, idling, until you hide it or the next
  spin's result phase ends. Useful for "place your bets" before a spin.
- **hide** — take it off screen. It won't cut a spin off mid-air: while the
  ball is rolling it answers 409 busy.
- **stop** — the panic button (the panel's **⏹ Stop**): aborts every
  animation and hides everything immediately. A spin that had already started
  still counts — it's committed to history (unless it was a `test` spin),
  since its result was already handed to whoever asked for it — and the game
  goes back to idle, ready for the next spin.

With **hide when idle** off, the wheel stays on screen between spins; a
`/hide` (or a stop) still takes it off until the next `/show` or spin.

### Config

```
curl http://localhost:4747/games/api/config
curl -X POST http://localhost:4747/games/api/config -H "Content-Type: application/json" -d "{\"roulette\":{\"spin_seconds\":12,\"cooldown_seconds\":30}}"
```

The config is stored as `{"roulette": {...}}`. `POST` merges whatever subset
you send into it, validates, saves, pushes it to every overlay and panel, and
returns the full config. Unknown keys are dropped, numbers are clamped to
their range, a bad colour becomes `""` (theme default) and an unknown choice
falls back to its default. There's no wheel setting — the wheel is always
American double-zero.

#### Config keys

Keys marked *per spin* are the **appearance** keys — the only ones allowed in
a spin's `overrides`, and what the Edit Mode editor edits.

| Key | Default | Values | Per spin |
| --- | --- | --- | --- |
| `x`, `y` | `50`, `50` | 0–100 — wheel centre, % of the 1920×1080 canvas | ✓ |
| `scale` | `1.25` | 0.2–5 — × the 400 px wheel | ✓ |
| `theme` | `"classic"` | `classic` \| `neon` \| `midnight` \| `royal` | ✓ |
| `red_color`, `black_color`, `green_color` | `""` | `""` = theme default, else `#rrggbb` | ✓ |
| `spin_seconds` | `9` | 4–30 — launch to landing | |
| `result_seconds` | `6` | 1–120 — how long the result stays up | |
| `hide_when_idle` | `true` | `false` keeps the wheel on screen all the time | |
| `show_result` | `true` | show the result badge | ✓ |
| `result_position` | `"center"` | `center` (over the turret) \| `below` \| `above` | ✓ |
| `result_details` | `true` | the `ODD · LOW · 2ND 12` line under the number | ✓ |
| `show_history` | `true` | the strip of recent results under the wheel | ✓ |
| `history_count` | `10` | 1–20 — how many results the strip shows | ✓ |
| `show_user` | `true` | the `@user spins` caption when a spin has a user | ✓ |
| `show_bets` | `true` | the winners list after landing (only when bets were sent) | ✓ |
| `bets_max` | `5` | 1–20 — winners listed | ✓ |
| `sfx` | `true` | built-in synthesized ball sounds in the overlay | ✓ |
| `sfx_volume` | `0.5` | 0–1 | ✓ |
| `spin_clip`, `land_clip` | `""` | soundboard clip names fired at launch / landing (`""` = none) | |
| `cooldown_seconds` | `0` | 0–3600 — extra lockout after the result phase ends (`0` = none) | |

---

## WebSocket protocol

The overlay connects to `/games/ws/overlay`, the panel to `/games/ws/panel`;
both reconnect on their own. You only need this if you're building your own
overlay or dashboard — bots should use the HTTP API.

Server → client (JSON text frames):

- `{"type": "config", "config": {"roulette": {...}}}` — on connect and after
  every config save.
- `{"type": "state", "game": "roulette", "state": STATE}` — on connect and on
  every transition: spin start, landing, result end, cooldown end, show,
  hide, stop, history clear.
- `{"type": "stop"}` — hide and abort everything now (a `state` message
  follows). `/games/api/{game}/stop` sends `{"type": "stop", "game": "roulette"}`
  instead, which only concerns that game.

`STATE` is the same object `GET /games/api/status` returns per game:

```
{
  "state": "spinning",
  "visible": true,
  "spin": {"id": "r-1a2b3c4d", "result": {...}, "seed": 123456789, "duration_ms": 9000,
           "elapsed_ms": 2140, "user": "hexmod", "bets": [...], "overrides": {}, ...},
  "last": {...spin object...},
  "history": [{"number": "32", "color": "red", ...}, ...],
  "busy_ms": 12860
}
```

- `state` — `idle`, `spinning`, `result` or `cooldown`.
- `visible` — whether the wheel should be on screen: during a spin and its
  result, after `/show`, or always when `hide_when_idle` is off.
- `spin` — the spin in the air or in its result phase (`null` when idle),
  with `elapsed_ms`: milliseconds since it started, measured on the server
  when the message was built. Clients seek by `elapsed_ms` and never compare
  epoch times, so clock skew between machines doesn't matter.
- `last` — the last committed spin.
- `history` — up to 20 results, newest first. It only ever contains
  **landed** spins, so it never spoils the one in the air.
- `busy_ms` — how long until the game accepts a new spin.

Client → server: `{"type": "hello", "game": "roulette"}` (optional) and
`{"type": "error", "message": "..."}` — overlays report script errors this
way and they land in `hexcast.log`. Unknown types are ignored.

`GET /games/api/status` returns
`{"ok": true, "connected": true, "overlays": 1, "games": {"roulette": STATE}}`,
where `overlays` is the number of connected games overlays — the top bar's
Games dot uses it.

---

## Bot recipes

Any bot that can make an HTTP request can run roulette. It has to run **on
your network** — Hexcast is LAN-only, so a cloud bot (Nightbot,
StreamElements, Fossabot) can't reach it. Streamer.bot, Firebot, or a script
of your own on the streaming PC (or any machine on the LAN) all work;
substitute your bot's variables for the `{user}`-style placeholders below. If
the bot runs on a different machine from Hexcast, use Hexcast's LAN address
(e.g. `http://192.168.1.20:4747`) instead of `localhost`.

### One command, one spin

`!spin` — anyone spins, just for fun:

```
GET http://localhost:4747/games/api/roulette/spin?user={user}
```

If the response is a 409, reply "the wheel is busy". Otherwise stay quiet and
let the overlay do the talking.

### One command, one bet

`!roulette red 100` — each viewer spins their own wheel with one bet:

```
GET http://localhost:4747/games/api/roulette/spin?user={user}&bet={bet}&amount={amount}&wait=true
```

1. Take the stake from the viewer's points in your bot, and URL-encode
   whatever they typed for `{bet}`.
2. The call returns when the ball lands. `bets[0].valid`, `bets[0].win` and
   `bets[0].returned` tell you everything: credit `returned` back (a win pays
   stake + winnings, an invalid bet is refunded, a loss is `0`).
3. Announce `result.number`, `result.color` and whether they won.
4. On a 409, refund the stake and reply with `retry_in_ms`.

### A chat betting round

Collect bets from chat, then spin once for everyone — the classic chat-casino
command flow:

1. **Open the table.** A mod types `!openbets`; the bot calls
   `GET /games/api/roulette/show` so the wheel sits on screen, and starts an
   empty bet list.
2. **Take bets.** For each `!bet <bet> <amount>`, the bot calls
   `GET /games/api/roulette/validate?bet=<bet>`. If `valid` is `false`, reply
   with `error` and don't take the stake; otherwise take the stake, add
   `{"user", "bet", "amount"}` to the list, and confirm with `label` and
   `odds` (`@bob 100 on Split 17/20, pays 17 to 1`).
3. **Spin.** A mod types `!spin`; the bot posts every collected bet in one
   call, with `wait: true`:

   ```json
   POST /games/api/roulette/spin
   {"user": "hexmod", "wait": true, "bets": [ ...the collected bets... ]}
   ```

4. **Pay out and announce.** When the response arrives the ball has just
   landed. Credit every bet's `returned`, then post the winners —
   `result.number`, `result.color`, and each bet with `"win": true` —
   and `summary` for a recap. The overlay shows the same winners list.
5. On a **409**, keep the bet list and try again after `retry_in_ms`.

The same flow as a small Python sketch (any HTTP library works — `pip
install requests` for this one):

```python
import requests

HEX = "http://localhost:4747/games/api/roulette"
pending = []   # bets collected this round

def on_bet(user, bet, amount):
    """!bet red 100  ->  on_bet("bob", "red", 100)"""
    v = requests.get(f"{HEX}/validate", params={"bet": bet}).json()
    if not v.get("valid"):
        return f"@{user} {v.get('error', 'invalid bet')}"
    pending.append({"user": user, "bet": bet, "amount": amount})
    return f"@{user} {amount} on {v['label']}, pays {v['odds']} to 1"

def on_spin(mod):
    """!spin  ->  one spin for every collected bet, answered when the ball lands"""
    global pending
    bets, pending = pending, []
    r = requests.post(f"{HEX}/spin", json={"user": mod, "wait": True, "bets": bets}, timeout=60)
    data = r.json()
    if r.status_code == 409:
        pending = bets + pending          # keep the round for the next try
        return f"The wheel is busy, try again in {data['retry_in_ms'] // 1000 + 1}s"
    if not data.get("ok"):
        pending = bets + pending
        return f"Roulette error: {data.get('error', r.status_code)}"
    res = data["result"]
    winners = [b for b in data["bets"] if b["win"]]
    # credit b["returned"] for every bet here - Hexcast keeps no balances
    names = ", ".join(f"@{b['user']} +{b['returned']}" for b in winners) or "nobody"
    return f"{res['number']} {res['color'].upper()}! Winners: {names}"
```

### Without `wait`

If your bot can't hold a request open for the length of a spin, call spin
without `wait`, keep the response (it already holds the result and every
resolved bet), and post the announcement at `lands_at` — or simply after
`duration_ms`. Don't post it straight away; it would spoil the spin.

---

## Storage

Settings persist in `config/games.json` (the folder follows
`HEXCAST_CONFIG_DIR` if you've set it). Spin history and stats are in-memory
only — up to 200 spins, reset on restart. No tokens, no secrets — same
security posture as the rest of Hexcast: no auth, keep it on the LAN.

---

## Troubleshooting

**`/games` or the API gives a 404.** The module loads at startup — restart
Hexcast after adding `games.py`, and check the console output for errors.

**The wheel never shows up in OBS.** Hover the Games dot in the top bar: "no
overlay connected" means the browser source isn't connected
(`GET /games/api/status` shows the same as `overlays`). Make sure the URL is
`/games/overlay` (and any `?game=` is `roulette`), **Shutdown source when not
visible** is off, and the source is in the live scene. Right-click →
**Interact** → **Ctrl+Shift+R** reloads it.

**The wheel is cut off, or too big or small.** It's placed by its centre, and
scale 1 is 400 px on the 1920×1080 canvas. A result badge or history strip
positioned *below* or *above* sits outside the wheel, so leave room for it.
Use **Edit Mode** and **Test in OBS** to check before saving.

**No ball sounds.** Turn on **Control audio via OBS** on the browser source
and look for it in the Audio Mixer; check **Built-in ball sounds** and
**Built-in sound volume** in the panel's Settings (a spin's `overrides` can
also carry `sfx` / `sfx_volume`). In a normal browser tab, sound is blocked until you click the
page once (see the Help page's browser notes). The panel's preview is always
silent by design.

**The launch/landing clip doesn't play.** It plays on the soundboard overlay
(`/overlay`), which must be in the scene. The name has to match a clip in your
library (the settings field suggests them), test spins never fire cues, and a
clip that's still in its own cooldown is suppressed.

**A spin returns 409 "busy".** The previous spin is still going — spinning,
showing its result, or in cooldown. The response tells you which and how long
(`retry_in_ms`). **⏹ Stop** (or `/games/api/stop`) ends it at once.

**A bet comes back `valid: false`.** Read its `error`. The usual suspects:
split numbers have to touch on the table (`18/19` don't); a corner has to be
a 2×2 block (its lowest number can't be in column 3); the only zero bets are
the ones listed in the [bet reference](#bet-reference) — so `0/3`, `0/2/3` and
first four (`0/1/2/3`) aren't bets here; use the basket, `0/00/1/2/3`.

**History is empty after a restart.** By design — history and stats live in
memory. Settings persist.

**Test spins aren't in the history.** Also by design: `test` spins are shown
on stream but never counted, and never fire clip cues.
