# Russian Roulette — a revolver, a stuffed dummy, and chat's coins

A side-on revolver points at a stuffed burlap dummy with a name tag. Chat bets
against the **bank** (your bot and its coin bank) on whether the dummy
survives each pull. **Pull k loads k bullets** and the cylinder is re-spun, so
the danger climbs every round. A bang ends the game: muzzle flash, stuffing
bursts out, the dummy crumples off its post — and at the end of the game-over
card it's stuffed again and climbs back up.

The **server** picks every outcome; the overlay only animates it. Everything
lives under `/games/api/russian/*`, and the game has its own tab on the Games
panel: `/games#russian`. (It is an add-on of the [Games](games.md) plugin: install it from the **+** in the Games tab.) The header's name is yours (`title`, default
**Russian Roulette**) — see [Edit Mode & branding](#edit-mode--branding).

---

## At a glance

| | |
| --- | --- |
| Pulls per game | 3 (setting `rounds`, 1–5) |
| Pull k | k bullets of 6, re-spun: the dummy is shot with chance k/6 |
| Bets | **survive** (rides from pull to pull and grows) · **bang** (this pull fires) |
| House edge | 5% (setting `house_edge_pct`), taken once per stake |
| Timings | 30 s first bet window · 20 s before each later pull · ~9 s pull · 4 s result · 10 s game-over card |
| No bets | when a window closes with nothing on the line, the game ends |
| Volunteer | the dummy's `dummy_user` gets 5% of the bank's net win for the game |
| Typical game | ~1:30 (longest ~1:50 with 3 pulls) |

---

## Fairness

- Each pull the server adds one bullet to a random empty chamber, then spins
  the cylinder to a uniformly random chamber (`secrets.SystemRandom()`, the
  operating system's cryptographic random source). The pull fires when that
  chamber is loaded — chance **k/6** on pull k, whatever happened before.
- The overlay and panel only **animate** the server's result. Nothing — the
  API, the panel, a bet — can pick or nudge an outcome.
- The outcome is sent to the overlay when the trigger sequence starts. Bets
  and cash-outs are already closed by then (`409 bets_closed`), so knowing it
  early is worth nothing.
- Nothing on screen gives it away before the hammer drops. The revolver is shown side-on (no chamber is
  ever visible once the cylinder is closed), and the spin is a seeded amount that has nothing to do with
  which chamber fires — so even a frame-by-frame replay of the spin can't tell. The only tells are the
  hammer cocking back and, at the end of the pull, BANG or *click*.

---

## The bets (all against the bank)

**survive — "it clicks."** A stake rides from pull to pull. The house edge is
taken **once**, when the stake goes in; after that every survived pull grows it
at the fair rate. A stake placed before pull *j* is worth, once the dummy has
survived pull *c*:

```
stake × (1 − edge) × alive(j − 1) / alive(c)
alive(c) = chance the dummy survives pulls 1..c = (5/6)·(4/6)·…·((6−c)/6)
```

Between pulls a rider can **cash out** (credited now), **leave it in**, or
**add more** (the added coins start at the current pull's price). A bang wipes
every survive stake. Riders still in after the last pull are cashed out
automatically.

**bang — "this pull fires."** One pull only. Pays `stake × (1 − edge) × 6 / k`
on pull k; lost on a click.

With the defaults (3 pulls, 5% edge) — multipliers are the total paid back,
stake included:

| Pull | Bullets | Fires | Fresh survive bet | Riding since pull 1 | Bang |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | 17% | ×1.14 | ×1.14 | ×5.70 |
| 2 | 2 | 33% | ×1.42 | ×1.71 | ×2.85 |
| 3 | 3 | 50% | ×1.90 | ×3.42 | ×1.90 |

Winnings are rounded **down** to whole coins. `GET /games/api/russian/bets`
returns the live table (it follows the settings).

**Why riding grows faster than cash-out-and-rebet:** a fresh stake pays the
edge every time it goes in; a ride pays it once. That's the reward for nerve.

### Limits

- `min_bet` / `max_bet` apply per player **per pull, per side** (what went down
  in this window).
- `max_payout` (0 = off): a survive stake worth that much is cashed out at the
  cap after the pull it reached it on.
- 500 players per game.

---

## Timeline of a game

```
POST /start ─► betting (30 s: open_bet_seconds)   rules box · "Bets close in 10!"
                 │  nothing on the line when it closes → game over ("no bets")
                 ▼
               pulling (~9 s: pull_seconds)        swing out · load bullet k · snap shut ·
                 │                                  spin · cock the hammer · … · pull
                 ▼
               result (4 s)        BANG! → bang bets paid, survive stakes lost → over
                 │                 click → bang bets lost, survive stakes grow
                 ▼
               betting (20 s: between_seconds)     ride · add · cash out · new bets
                 │  … up to `rounds` pulls
                 ▼
               over (10 s: summary_seconds)        winners, totals, volunteer cut, revive
                 ▼
               idle
```

`POST /games/api/russian/next` (or the panel's **Skip ▶**) ends the current
phase right away — handy for testing.

**Game endings** (`outcome`): `bang` · `survived` (every pull clicked; riders
cashed out) · `walked` (everyone cashed out, nobody bet — the dummy lives) ·
`no_bets` (nobody bet at all) · `stopped` (`/stop`) · `restart` / `error`
(settled after Hexcast stopped mid-game or a rule failed).

---

## The dummy and the volunteer

The name on the dummy's tag comes from `/start` (`dummy`), from
`POST /games/api/russian/dummy` (before the first pull, or for the next game
when none is running), or the `dummy_name` setting.

`dummy_user` is the chatter who **volunteered**. When the game ends on its own
(`bang`, `survived`, `walked`) and the bank came out ahead, the volunteer is
credited `volunteer_cut_pct` (5%) of the bank's net for that game — a ledger
`credit` with reason `volunteer_cut`. Nothing when the bank lost, and nothing
for `stopped` / `no_bets` games.

Picking the volunteer is your bot's job (e.g. volunteers first, else a random
chatter). If you pick random chatters, give them a way to opt out.

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
| `GET\|POST /games/api/russian/start` | `{dummy, dummy_user, rounds (1–5), seconds (first window), test}` → `{started, state}`; **409** while a game runs |
| `GET\|POST /games/api/russian/bet` | `{user, side, amount}` — `side`: `survive` (also `live`, `lives`, `alive`, `click`, `safe`) or `bang` (also `die`, `dies`, `shot`, `fire`, `dead`) → `{side, amount, debits:[{user, amount, seq, reason}], player}`; betting the same side again adds to it; **409** `bets_closed` outside a betting window |
| `GET\|POST /games/api/russian/cashout` | `{user}` → `{credits, ledger}`: the survive stake's value now (a stake placed this window comes back as a `refund`) |
| `GET\|POST /games/api/russian/remove` | `{user, side?}` — take back what went down in **this** window (not yet at risk) → refunds |
| `GET\|POST /games/api/russian/dummy` | `{dummy, dummy_user}` → `{dummy, applies: "this game" \| "next game"}` |
| `GET\|POST /games/api/russian/next` | end the current phase now (alias `/pull`) → `{skipped}` |
| `GET /games/api/russian/table` | the STATE (below) |
| `GET /games/api/russian/user/{name}` | `{player, session}` — the player's stakes now + session debits / credits / net |
| `GET /games/api/russian/ledger?since=0&limit=500` | this game's ledger events |
| `GET\|POST /games/api/russian/stop` | end the game now: a pull already in progress counts, bang bets are refunded, survive stakes cashed out at their current value |
| `GET /games/api/russian/history · last · bets · validate` | finished games + stats · the last one · rules + odds · dry-run `?side=&amount=` |
| `GET\|POST /games/api/russian/show · hide` | keep the idle scene on screen · hide it |
| `GET\|POST /games/api/russian/preview` | `{overrides: {appearance keys}, seconds (2–60, default 8)}` (or `x=&y=&scale=`) — the Edit Mode editor's **Test in OBS**: the scene goes on screen for `seconds` with that look on top of the saved config (`STATE.preview`), even while hidden, over whatever it shows (the idle scene between games). Never touches the game, bets or the ledger; expires on its own; `/hide`, `/stop` and `/preview/clear` end it |
| `GET · POST /games/api/config` | the `"russian"` section (every key in [Settings](#settings)) |

`/spin`, `/play` and `/timer` answer 400 for this game — a game starts with
`/start`, and a look is tried out on the overlay with `/preview`. So do
`/announce` and `/announce/clear`: the game-over card is built in (its numbers
are `STATE.game.summary`).

### STATE

`STATE.game` (null between games; `STATE.idle` then has the next game's dummy
and odds). `STATE.preview` is `{id, overrides, expires_in_ms}` while a Test in
OBS is up, else `null`.

```jsonc
{
  "id": "rr-1a2b3c4d", "test": false,
  "phase": "betting",               // betting | pulling | result | over
  "ends_in_ms": 12400, "phase_ms": 20000, "elapsed_ms": 7600,
  "round": 2, "rounds": 3, "survived": 1, "bullets": 2,
  "loaded": [1, 4],                 // chambers loaded so far
  "pull": {"round": 2, "new": 4, "loaded": [1, 4], "stop": 3, "fired": false, "seed": 42},  // pulling/result/over
  "dummy": {"name": "Bob", "user": "bob"},
  "odds": [{"round": 1, "bullets": 1, "fire_pct": 16.7, "survive": 1.14, "ride": 1.14, "bang": 5.7}, "..."],
  "players": [{"user": "alice", "stake": 100, "fresh": 0, "value": 114, "if_survives": 171, "bang": 0, "bang_pays": 0}],
  "at_risk": 114, "bang_total": 0,
  "last": {"round": 1, "fired": false, "winners": [], "losers": []},
  "outcome": null, "summary": null,  // set in "over"
  "currency": "coins", "min_bet": 1, "max_bet": 100000
}
```

### Ledger reasons

| type | reason | when |
| --- | --- | --- |
| debit | `bet` / `add` | a bet / more on the same side this window |
| credit | `win` | a bang bet on the pull that fired |
| credit | `cashout` | a survive stake cashed out (by the player, the cap, the end of the game, or `/stop`) |
| credit | `refund` | a bet taken back (`/remove`), or a bang bet returned by `/stop` |
| credit | `volunteer_cut` | the volunteer's share of the bank's win |

The ledger is the same one roulette and craps use: one `seq` numbering, each
event names its `game`. A bank running every game tails
`GET /games/api/ledger?since=<last_seq>` once (or `&game=russian` for this
one) — see [The shared ledger](games.md#the-shared-ledger).

---

## Your bot's side

Hexcast never reads chat. Your bot turns chat commands into API calls and pays
from the ledger. A suggested command set (put yours in the **Commands line**
setting so the overlay shows it):

| Chat | Call |
| --- | --- |
| `!rr` (mod) | pick the volunteer → `POST /start {dummy, dummy_user}` |
| `!live 100` / `!survive 100` | `POST /bet {user, side: "survive", amount: 100}` |
| `!bang 50` | `POST /bet {user, side: "bang", amount: 50}` |
| `!cashout` | `POST /cashout {user}` |
| `!takeback` | `POST /remove {user}` |

Before a `/bet`, check the player can afford it (the debit is Hexcast's word
that the stake was taken — debit it in your bot's bank when you see it in the
ledger, or right away from the `/bet` reply's `debits`, but never both).

---

## Settings

| Key | Default | |
| --- | --- | --- |
| `rounds` | 3 | pulls per game (1–5) |
| `house_edge_pct` | 5 | 0–25 |
| `open_bet_seconds` · `between_seconds` | 30 · 20 | betting windows |
| `pull_seconds` · `result_seconds` · `summary_seconds` | 9 · 4 · 10 | animation lengths |
| `min_bet` · `max_bet` · `max_payout` | 1 · 100000 · 0 | 0 = no max / no cap |
| `volunteer_cut_pct` | 5 | % of the bank's net win |
| `dummy_name` | Dummy | when a game starts without one |
| `currency` | coins | the name after amounts (your bot's coin), ≤ 24 characters |
| `commands_text` | "" | a line in the rules box, e.g. `!live 100 · !bang 50 · !cashout` |
| `pull_clip` · `click_clip` · `bang_clip` | "" | soundboard clips (play on the base `/overlay`) |
| `title` | "Russian Roulette" | the header's name — your branding (≤ 32 characters) ✓ |
| `x` · `y` · `scale` · `theme` | 50 · 50 · 1 · saloon | placement (0–100 % · 0–100 % · 0.2–5 × the 1100×560 scene) · `saloon`, `noir`, `neon` ✓ |
| `show_rules` · `show_players` · `players_max` · `show_odds` | on · on · 8 · on | the rules box · the players board · how many it lists · the payout ladder ✓ |
| `sfx` · `sfx_volume` | on · 0.6 | the overlay's own sounds ✓ |
| `hide_when_idle` | on | off = the idle scene stays on screen between games |

✓ = an appearance key: what the Edit Mode editor edits, and what a Test in OBS
(`/preview`) may override.

### Edit Mode & branding

Turn on **Edit Mode** (the hexbar) and click the Russian Roulette card (or its
**✎ Edit placement** button): the same placement & look editor as roulette and
craps. The real scene on a 16:9 preview of the 1920×1080 stage, fed a sample
game in its bet window (six sample players, the rules box, the payout ladder),
so you place what the stream will really show. Drag it (or use the 3×3 quick
grid), **Scale** 0.2–5, **Theme**, **Title**, the rules box and payout ladder,
the players board and how many it lists, sound and volume — all live in the
preview. **▶ Preview** plays a local demo pull (bets close → the pull → bang or
click → the game-over card, about 16 s; you can keep dragging while it plays);
**Test in OBS** puts the unsaved look on the real
overlay for 8 seconds (`/preview`); **Save** stores it and restyles every
overlay; **Reset** goes back to the defaults (not saved until Save). Cancel, ×,
Esc or a click outside throw the unsaved changes away.

`title` is text (never HTML), trimmed to 32 characters, shown upper-case in the
header, shrunk to fit and ended with "…" if it still doesn't; empty means the default. A plain emoji counts as one
character; a flag, a skin-tone emoji or a combined one (a family) counts as
several, and one that doesn't fit whole is dropped, never cut in half (the
same goes for the dummy's name, 24 characters). Set it in Edit Mode (to see
it live) or in the tab's Settings card.

---

## Storage & restarts

`config/games_russian.json` holds the running game (and the last 50 finished
ones + stats). Every change is written before its ledger events, with those
events as a journal, so a crash can't pay twice or lose a stake. A game still
running when Hexcast stops is **settled at the next start-up**: a pull already
in progress counts, then bang bets are refunded and survive stakes cashed out
at their value.
