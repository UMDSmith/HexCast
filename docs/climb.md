# Soul Climb — a damned soul, a hell pit, and chat's coins

A little damned soul tries to climb out of a pit in hell: left ledges, a
chimney, rusty chains, a frayed rope, a ladder of rib bones. Demons heckle him,
bats attack, lava geysers erupt, a skeleton grabs his ankle — and sooner or
later he falls (in one of seven undignified ways) or, once in a blue moon,
climbs out. Chat bets against the **bank** (your bot and its coin bank) on
**how high he gets**: a bet is a **height** and an **amount**. If his best
height reaches or passes your height you are paid the **multiplier** of that
height — the higher the guess, the bigger the multiplier — if he falls first the
stake is lost. The top of the pit is the escape: the jackpot.

The **server** decides how high he gets (with a published survival model, below);
the overlay only acts it out. Everything lives under `/games/api/climb/*`, and
the game has its own tab on the Games panel: `/games#climb`. (It is an add-on of
the [Games](games.md) plugin: install it from the **+** in the Games tab.) The
header's name is yours (`title`, default **Soul Climb**) — see
[Edit Mode & branding](#edit-mode--branding).

---

## At a glance

| | |
| --- | --- |
| Climbs per game | 1 (setting `climbs`, 1–3), each with its own betting window |
| Pit | `max_height` levels (10–300, default **100**); the top is the escape (default chance **2%**, setting `escape_pct`) |
| Bets | `{amount, height}` — up to **5 bets per player per climb**, at different heights; the same height again adds to it |
| Payout | amount × the height's multiplier, rounded **down** to whole coins; lost stake = gone |
| Multiplier | `(1 − house edge) / S(h)`, floored to 2 decimals, never below ×1.00 — every height's chance and multiplier is at `GET /games/api/climb/bets` and live on the overlay |
| House edge | 5% (setting `house_edge_pct`) |
| Timings | 30 s first bet window · 20 s before each later climb · the climb itself 6–80 s (it depends on how high he gets) · 8 s result · 12 s game-over card |
| No bets | when a window closes with nothing on the line, the game ends |
| Typical game | ~1:30 (one climb; the longest one-climb game is ~2:15) |

---

## Fairness

- The server decides the climb **once, when the betting window closes**, with
  `secrets.SystemRandom()` (the operating system's cryptographic random
  source). He starts at level 0 and tries one level after another: at each
  level the server rolls a number from 0 to 999 999 999 against that level's
  fall chance (written in parts per billion), until he falls or has made every
  level. His **best height** is the last level he made.
- The fall chances are the survival model below, nothing else. They rise with
  height (he tires, the wall gets worse), and the chance of getting out at all
  is the setting `escape_pct`. **Nothing** — the API, the panel, a bet, the
  amount of coins on the wall — can pick or nudge the outcome.
- **The comedy is only dressing.** After the outcome is drawn the server writes
  a *script* — a list of timed beats (climb, rest, a taunting demon, a slip
  that recovers, bats, a geyser, a dead end he backs out of, a hand that grabs
  his ankle, a rope that frays, the final fall…) — and the overlay replays it.
  The script is made from a separate seed and from the outcome, not the other
  way round: **no beat ever goes above his best height**, the beat that gets him
  there is the last climb beat, and a near fall is always on a ledge he has
  already been on. Nothing in the show decides, or changes, anything.
- The script is sent to the overlay when the climb starts. Bets are already
  closed by then (`409 bets_closed`), so knowing it early is worth nothing.
  Nothing on screen gives the end away before it happens either: the waiting
  demons, sleeping bats, lava vents and dead ends are on the wall *above* his
  best height too (they just never act), so the wall ahead of him looks the same
  wherever he is going to stop.
- **No hidden edge.** The multipliers are computed exactly (integer and
  fraction arithmetic, nothing rounded early) from the same parts-per-billion
  numbers the server rolls, and the table at `/bets` lists them all. Winnings
  are rounded down to whole coins, so the real return on small bets is a little
  lower than the table's.

---

## The pit (the survival model)

```
t        = i / max_height                              relative height of level i, 0..1
L        = −ln(escape_pct / 100)                       so that S(max_height) = escape_pct
Λ(t)     = L · (t + 0.9·t²) / 1.9                      cumulative hazard
p(i)     = exp(−(Λ(i/H) − Λ((i−1)/H)))                 chance he makes level i from level i−1
fall(i)  = ceil(10⁹ · (1 − p(i)))  parts per billion   what the server rolls (rounded up)
S(h)     = Π_{i ≤ h} (10⁹ − fall(i)) / 10⁹             chance his best height is at least h
mult(h)  = max(1.00, floor_to_cents((1 − edge) / S(h)))
pays     = floor(amount × mult(h))                      whole coins
```

`S(h)` is what the panel and `/bets` call the **chance**: the probability that
the soul gets at least as high as `h` — exactly the chance that a bet on `h`
wins. With the defaults (100 levels, 2% escape, 5% edge):

| Height | Chance S(h) | Multiplier | | Height | Chance S(h) | Multiplier |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 97.94% | ×1.00 | | 50 | 22.48% | ×4.22 |
| 3 | 93.85% | ×1.01 | | 60 | 14.92% | ×6.36 |
| 5 | 89.80% | ×1.05 | | 70 | 9.54% | ×9.95 |
| 10 | 79.90% | ×1.18 | | 80 | 5.88% | ×16.14 |
| 20 | 61.51% | ×1.54 | | 90 | 3.49% | ×27.18 |
| 30 | 45.64% | ×2.08 | | 95 | 2.66% | ×35.76 |
| 40 | 32.63% | ×2.91 | | **100 (escape)** | **2.00%** | **×47.50** |

His expected best height is about 32 (median 28). The multiplier is the *total*
paid back, stake included. For the first couple of levels the fair multiplier
with an edge would be under ×1.00; it is floored at **×1.00** (the stake back),
so a bet there wins nearly always and pays nothing extra — the house edge on
those heights is smaller than the setting, never negative.

`GET /games/api/climb/bets` returns the live table for **every** height (it
follows the settings) plus the rules in words; the overlay's payout ladder
(`show_odds`) and the multiplier flags on the wall come from the same numbers.

---

## The bets (all against the bank)

**A bet is a height and an amount.** `POST /games/api/climb/bet` with
`{user, amount, height}`. Heights are whole levels from 1 to `max_height`
(`40`, `"L40"`, `"level 40"` and `"h40"` all mean 40). If the soul's best
height is **at least** the bet's height, the bet pays `amount × mult(height)`
(credited when the climb ends); if he falls below it, the stake is lost. A
higher guess is harder to hit and pays more.

- **Up to 5 bets per player per climb**, each at a different height. Betting a
  height you already bet on **adds** to it (reason `add`). The sixth height is a
  400 that names the five you have.
- `min_bet` / `max_bet` apply per player **per climb** (all of a player's bets
  together, what went down in this window). `max_bet` 0 = no max.
- `max_payout` (0 = off): one winning bet pays at most this (never less than its
  own stake: a win doesn't lose money).
- 500 players per game.
- **Taking a bet back:** `POST /remove {user, height?}` returns what went down
  in this window (all the player's bets, or the one height) as a `refund`; only
  while the bets are open.
- One climb's result never carries to the next: every climb is a fresh window
  and fresh bets (a game with 3 climbs = 3 souls, 3 windows).

### Limits

The bet that is allowed is checked on the server (`GET /validate?height=&amount=`
is a dry run that also tells the multiplier and what it would pay). Bets close
the moment the climb starts: `409 bets_closed`.

---

## Timeline of a game

```
POST /start ─► betting (30 s: open_bet_seconds)   rules box · flags for every bet · "Bets close in 10!"
                 │  nothing on the line when it closes → game over ("no bets")
                 ▼
               climbing (the script + 0.6 s)      the soul at the foot of the wall · up · rests · demons ·
                 │                                 near falls · … · the fall (or the escape)
                 ▼
               result (8 s: result_seconds)        best height · winners paid · stakes lost
                 │
                 ▼
               betting (20 s: between_seconds)     the next soul, new bets   (… up to `climbs` climbs)
                 ▼
               over (12 s: summary_seconds)        every climb, winners, totals
                 ▼
               idle
```

`POST /games/api/climb/next` (or the panel's **Skip ▶**) ends the current phase
right away — handy for testing: it closes the bets in a window, finishes a climb
that is being acted out (the result is already decided).

**Game endings** (`outcome`): `complete` (every climb played) · `no_bets` (a
window closed with nothing on the line) · `stopped` (`/stop`) · `restart` /
`error` (settled after Hexcast stopped mid-game or a rule failed).

A game with several climbs plays them in a row; a window with no bets after
at least one climb ends the game as `complete`.

---

## The soul and the show

Each climb has its own soul: the name comes from `/start` (`soul`, several names
separated by commas = one per climb) or the `soul_name` setting, else a
built-in list (Gary, Kevin, Brenda, Steve…); every soul has its own skin tone
and outfit. The overlay draws him from shapes — big head, stub horns, a cracked
halo, a tail, three-fingered mitts — and animates the limbs, so every climb
looks a little different. What he does is in the **script** (below); how it is
dressed up is up to the overlay: captions and speech-bubbles come from pools
picked by the script's seeds, props (the lava, bones, chains, a cauldron, a
sausage grinder, a demon's giant hand) come from the beat that uses them.

The show is paced by the bets: the soul climbs slowly and with effort near a
height somebody bet on, quickly when the next flag is far away; every bet is a
**flag** on the wall, with the name of the player(s), that turns green when he
reaches it and grey when he falls short of it. A height meter, the multiplier
of the flags near him, a result card and a winners list round it off; the
overlay's own sounds (WebAudio, `sfx`, `sfx_volume`) go with each beat.

**Falls** (`style`): bonking off every ledge on the way down · a very long "oh
no" and a splash · straight into a cauldron · flicked off by a hex demon · into
a pit sausage grinder · a trampoline · a parachute umbrella. **Last words**
(`cause`) match what was on the wall: a snapped rope, a cracked ledge, a rotten
rib, a bony hand, bats, a geyser, a demon's nudge, tired arms.

### The script

`STATE.game.script` (during `climbing`, `result` and `over`; `null` before) —
the overlay replays it from `elapsed_ms`, so a browser that joins late is in
the right place:

```jsonc
{
  "v": 1, "seed": 1234567, "height": 100, "max": 37,    // max = his best height: the OUTCOME
  "escaped": false, "cause": "bat", "style": "cauldron", // cause/style are null when he escapes
  "routes": [{"from": 0, "to": 9, "kind": "ledge"}, {"from": 9, "to": 21, "kind": "chains"}, "..."],
  "duration_ms": 41230,
  "beats": [
    {"t": 0,    "d": 1500, "ev": "ready", "lv": 0, "to": 0, "s": 818231},
    {"t": 1500, "d": 360,  "ev": "climb", "lv": 0, "to": 1, "s": 5521},
    {"t": 1860, "d": 1200, "ev": "slip",  "lv": 5, "to": 3, "k": 2, "s": 9921},   // a near fall
    "..."
  ]
}
```

| `ev` | | extra keys |
| --- | --- | --- |
| `ready` `climb` `cheer` | at the foot · one level up · a flag's level reached | `cheer`: `flags` |
| `idle` `rest` | a shrug, a wipe, a pant, a gulp, a wave, a flex · a sit-down or a lean | `kind` |
| `taunt` `bat` `geyser` `grab` `fray` | a demon heckles · bats · lava vent · a skeleton hand · a strand of the rope pops (and holds) | `kind` `side` · `n` · `side` |
| `slip` | a loose rock gives: he slides down `k` (1–3) levels and catches himself | `k` |
| `deadend` | a wrong turn up a spiky dead end and back; `peak` is never above his best height | `peak` `side` |
| `fatal` `fall` | the last words · the fall in a `style` (bonk adds `n` bonks) | `cause` · `style` `n` |
| `escape` | out of the pit: the victory pose | |

Every beat has `t` (start, ms), `d` (length, ms), `lv`/`to` (the level it
begins and ends at — each beat starts where the last one ended, never above
`max`, and the last climb beat reaches `max`) and `s` (a seed for its own
random bits). A script is capped at 110 s: a very long climb is replayed
evenly faster. `climb_speed` (0.5–2) is a plain speed factor.

---

## Test games

`/start` with `"test": true` (the panel's **Test game**) plays exactly the same
but **writes nothing to the ledger**: the overlay says `TEST GAME · NO COINS`,
the coin movements only show in the game's own log, and test games don't count
in the stats. Use them to rehearse without touching anyone's coins.

---

## HTTP API

Every call returns `{"ok": true|false, ...}`; GET query parameters and a POST
JSON body both work (the body wins). Actions return the game's fresh `state`.

| Call | What it does |
| --- | --- |
| `GET\|POST /games/api/climb/start` | `{soul, climbs (1–3), seconds (first window), test}` → `{started, state}`; `soul` is a name, or several separated by commas (one per climb); **409** while a game runs |
| `GET\|POST /games/api/climb/bet` | `{user, amount, height}` → `{height, side: "level 40", amount, total, mult, pays, debits:[{user, amount, seq, reason}], player}`; `height` may also be given as `level`, `guess` or `bet`; the same height again adds; **409** `bets_closed` outside a betting window; **400** for a bad height, amount or a sixth height |
| `GET\|POST /games/api/climb/remove` | `{user, height?}` — take back what went down in **this** window (all of the player's bets, or one height) → `{credits, ledger}` (refunds) |
| `GET\|POST /games/api/climb/next` | end the current phase now → `{skipped}` |
| `GET /games/api/climb/table` | the STATE (below) |
| `GET /games/api/climb/user/{name}` | `{player, session}` — the player's bets now + session debits / credits / net |
| `GET /games/api/climb/ledger?since=0&limit=500` | this game's ledger events |
| `GET\|POST /games/api/climb/stop` | end the game now: a climb already decided counts (winners are paid), an open window is refunded |
| `GET /games/api/climb/history · last · bets · validate` | finished games + stats · the last one · rules + the table of every height (chance, multiplier) · dry-run `?height=&amount=` |
| `GET\|POST /games/api/climb/show · hide` | keep the idle scene on screen · hide it |
| `GET\|POST /games/api/climb/preview` | `{overrides: {appearance keys}, seconds (2–60, default 8)}` (or `x=&y=&scale=`) — the Edit Mode editor's **Test in OBS**: the scene goes on screen for `seconds` with that look on top of the saved config (`STATE.preview`), even while hidden, over whatever it shows. Never touches the game, bets or the ledger; expires on its own; `/hide`, `/stop` and `/preview/clear` end it |
| `GET · POST /games/api/config` | the `"climb"` section (every key in [Settings](#settings)) |

`/spin`, `/play` and `/timer` answer 400 for this game — a game starts with
`/start`, and a look is tried out on the overlay with `/preview`. So do
`/announce` and `/announce/clear`: the game-over card is built in (its numbers
are `STATE.game.summary`).

### STATE

`STATE.game` (null between games; `STATE.idle` then has the next game's soul
and odds). `STATE.preview` is `{id, overrides, expires_in_ms}` while a Test in
OBS is up, else `null`.

```jsonc
{
  "id": "cl-1a2b3c4d", "test": false,
  "phase": "betting",               // betting | climbing | result | over
  "ends_in_ms": 12400, "phase_ms": 30000, "elapsed_ms": 17600,
  "climb": 1, "climbs": 1, "height": 100,
  "soul": {"name": "Gary", "seed": 1234, "skin": 2, "gear": 5},
  "edge_pct": 5, "escape_pct": 2,
  "markers": [{"height": 40, "total": 300, "chance": 0.326259, "mult": 2.91,
               "users": [{"user": "alice", "amount": 200}, {"user": "bob", "amount": 100}]}],   // the flags
  "players": [{"user": "alice", "stake": 250, "bets": [{"height": 40, "amount": 200, "mult": 2.91, "pays": 582}, "..."]}],
  "at_risk": 450,
  "odds": [{"height": 5, "chance_pct": 89.8, "mult": 1.05}, "..."],       // the overlay's payout ladder
  "script": null,                   // the climb's script while climbing / result / over (above)
  "last": {"climb": 1, "max": 37, "escaped": false, "cause": "bat", "style": "cauldron", "soul": "Gary",
           "winners": [{"user": "alice", "height": 20, "amount": 50, "mult": 1.54, "pays": 77}, "..."],
           "losers": [{"user": "bob", "height": 40, "amount": 100}, "..."]},
  "results": [{"climb": 1, "max": 37, "escaped": false, "cause": "bat", "style": "cauldron", "soul": "Gary"}],
  "outcome": null, "summary": null, // set in "over"
  "currency": "coins", "min_bet": 1, "max_bet": 100000, "max_bets": 5, "commands_text": ""
}
```

### Ledger reasons

| type | reason | when |
| --- | --- | --- |
| debit | `bet` / `add` | a bet / more on the same height this window |
| credit | `win` | a bet whose height he reached, when the climb ends: amount × multiplier, rounded down |
| credit | `refund` | a bet taken back (`/remove`), or a window's bets returned by `/stop` |

Each event's `bet_id` is `<game id>/c<climb>/h<height>` and its `label`
`Height 40 · climb 1` (a win adds the multiplier: `· x2.91`). The ledger is the
same one every game uses: one `seq` numbering, each event names its `game`. A
bank running every game tails `GET /games/api/ledger?since=<last_seq>` once (or
`&game=climb` for this one) — see [The shared ledger](games.md#the-shared-ledger).

---

## Your bot's side

Hexcast never reads chat. Your bot turns chat commands into API calls and pays
from the ledger. A suggested command set (put yours in the **Commands line**
setting so the overlay shows it):

| Chat | Call |
| --- | --- |
| `!climb` (mod) | `POST /start {soul: "<a name>"}` |
| `!climb 100 40` | `POST /bet {user, amount: 100, height: 40}` |
| `!takeback` | `POST /remove {user}` |
| `!odds` | `GET /bets` — the table of every height |

Before a `/bet`, check the player can afford it (the debit is Hexcast's word
that the stake was taken — debit it in your bot's bank when you see it in the
ledger, or right away from the `/bet` reply's `debits`, but never both). The
`/bet` reply already says the multiplier and what the bet would pay.

---

## Settings

| Key | Default | |
| --- | --- | --- |
| `climbs` | 1 | climbs per game (1–3), each with its own betting window |
| `max_height` | 100 | levels in the pit (10–300); the top is the escape |
| `escape_pct` | 2 | the chance the soul makes it out (0.1–50 %); sets how fast the odds fall |
| `house_edge_pct` | 5 | 0–25 |
| `open_bet_seconds` · `between_seconds` | 30 · 20 | betting windows (5–300) |
| `result_seconds` · `summary_seconds` | 8 · 12 | the result card (2–30) · the game-over card (4–60) |
| `climb_speed` | 1.0 | 0.5–2: how fast the show is replayed (a long climb is capped at 110 s anyway) |
| `min_bet` · `max_bet` · `max_payout` | 1 · 100000 · 0 | per player per climb · 0 = no max / no cap |
| `soul_name` | "" | the soul's name, or several separated by commas (one per climb, ≤ 24 characters each); empty = a built-in list |
| `currency` | coins | the name after amounts (your bot's coin), ≤ 24 characters |
| `commands_text` | "" | a line in the rules box, e.g. `!climb 100 40` |
| `climb_clip` · `fall_clip` · `escape_clip` | "" | soundboard clips (play on the base `/overlay`) |
| `title` | "Soul Climb" | the header's name — your branding (≤ 32 characters) ✓ |
| `x` · `y` · `scale` · `theme` | 50 · 50 · 1 · inferno | placement (0–100 % · 0–100 % · 0.2–5 × the 880×960 scene) · `inferno`, `abyss`, `sulfur` ✓ |
| `show_rules` · `show_players` · `players_max` · `show_odds` | on · on · 8 · on | the rules box · the players board · how many it lists · the payout ladder ✓ |
| `sfx` · `sfx_volume` | on · 0.6 | the overlay's own sounds ✓ |
| `hide_when_idle` | on | off = the idle scene stays on screen between games |

✓ = an appearance key: what the Edit Mode editor edits, and what a Test in OBS
(`/preview`) may override. A change to the pit (`max_height`, `escape_pct`,
`house_edge_pct`, `max_payout`, `climb_speed`) applies from the **next** game: a
game that is running keeps the rules it started with, so the odds on screen
never change under a bet.

### Edit Mode & branding

Turn on **Edit Mode** (the hexbar) and click the Soul Climb card (or its **✎
Edit placement** button): the same placement & look editor as the other games.
The real scene on a 16:9 preview of the 1920×1080 stage, fed a sample game in its
bet window (six sample players, flags on the wall, the rules box, the payout
ladder), so you place what the stream will really show. Drag it (or use the 3×3
quick grid), **Scale** 0.2–5, **Theme** (Inferno's red lava, Abyss's cold blue
fire, Sulfur's yellow brimstone), **Title**, the rules box and payout ladder,
the players board and how many it lists, sound and volume — all live in the
preview. **▶ Preview** plays a local demo climb (bets close → the climb → a fall
→ the result card → the game-over card, about 30 s; you can keep dragging while
it plays); **Test in OBS** puts the unsaved look on the real overlay for 8 seconds
(`/preview`); **Save** stores it and restyles every overlay; **Reset** goes back
to the defaults (not saved until Save). Cancel, ×, Esc or a click outside throw
the unsaved changes away.

`title` is text (never HTML), trimmed to 32 characters, shown upper-case in the
header, shrunk to fit and ended with "…" if it still doesn't; empty means the
default. A plain emoji counts as one character; a flag, a skin-tone emoji or a
combined one (a family) counts as several, and one that doesn't fit whole is
dropped, never cut in half (the same goes for the soul's name, 24 characters).
Set it in Edit Mode (to see it live) or in the tab's Settings card.

---

## Storage & restarts

`config/games_climb.json` holds the running game (and the last 50 finished ones +
stats). Every change is written before its ledger events, with those events as a
journal, so a crash can't pay twice or lose a stake. A game still running when
Hexcast stops is **settled at the next start-up**: a climb that was already
decided (the soul was on his way up) counts and the winners are paid, then every
bet of an open window is refunded.
