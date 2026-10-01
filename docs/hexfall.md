# Hexfall — a hex-themed Plinko and chat's coins

One glowing skull drops through a pyramid of hexagonal pegs and falls into
a slot at the bottom. Chat bets against the **bank** (your bot and its coin
bank) before every drop; the slot the skull lands in pays the bet **× its
multiplier** — or **busts** (×0). It tumbles from peg to peg, spinning as it bounces, every peg flashes and clicks, the landing slot blazes, and the winners
are listed.

The **server** picks every path; the overlay only animates it. Everything
lives under `/games/api/hexfall/*`, and the game has its own tab on the Games
panel: `/games#hexfall`. (It is an add-on of the [Games](games.md) plugin:
install it from the **+** in the Games tab.) The header's name is yours
(`title`, default **Hexfall**) — see [Edit Mode & branding](#edit-mode--branding).

---

## At a glance

| | |
| --- | --- |
| Drops per game | 3 (setting `drops`, 1–10) |
| Board | `rows` rows of hexagonal pegs (8–16, default 12) → `rows + 1` slots |
| Bets | one amount per drop (`min_bet` / `max_bet` per player per drop, default 1 and 250 — both are settings in the panel and in `/games/api/config`); betting again adds to it |
| Payout | bet × the landing slot's multiplier, rounded **down** to whole coins; ×0 is a **bust** |
| Multipliers | a built-in table per rows × risk (`low`, `medium`, `high`), or your own list |
| House edge | **computed**, never set: 100% − the table's return. Default table (12 rows, medium): **4.66%** |
| Timings | 30 s first bet window · 20 s before each later drop · ~9 s fall · 5 s result · 10 s game-over card |
| No bets | when a window closes with nothing on the drop, the game ends |
| Typical game | ~2:00 (3 drops) |

---

## Fairness

- On every drop the server flips **one fair coin per row** — left or right, a
  bit from `secrets.SystemRandom()`, the operating system's cryptographic
  random source. The slot is the number of right bounces, so slot *k* of *n*
  rows has the binomial probability **C(n, k) / 2ⁿ**, whatever happened on any
  earlier drop. (Rows 12: the middle slot is 924 / 4096 = 22.56%, each edge
  slot 1 / 4096.)
- The overlay and panel only **animate** the server's path: `fall.path` lists
  the bounce at every peg (0 = left, 1 = right). The skull touches exactly
  those pegs and ends in exactly that slot. The drop's `seed` only varies the
  cosmetic jitter — hop heights, hop times, sparks — never where it goes.
  Nothing — the API, the panel, a bet — can pick or nudge an outcome.
- The path is sent to the overlay when the skull is released. Bets are already
  closed by then (`409 bets_closed`), so knowing it early is worth nothing;
  the next drop's path does not exist until its own window closes. Nothing on
  screen gives it away before the skull gets there: the pegs light up behind
  and around the skull (the same on both sides), and the pockets glow the same
  whatever is coming.
- **No hidden edge.** There is no house-edge setting and no payout scaling:
  the multiplier table *is* the odds. The return and the house edge shown on
  the overlay, in the panel and in `/bets` are computed exactly (fractions, not
  floats) from those binomial probabilities. Winnings are rounded down to whole
  coins, so the real return on small bets is a little lower than the table's.

---

## The multiplier table

Each slot carries a multiplier; slot *k* (counting from the left) is where the
skull ends after *k* right bounces. **Return** (RTP) is the sum over the slots
of probability × multiplier; the **house edge** is what is left of 100%
(it is shown rounded so the two always add up, and is negative for a table that
pays back more than 100%).

The built-in tables are **not symmetric**. The **centre slot** — the likeliest
one — holds the **top payout** (with an odd number of rows there is no single
centre: one of the two middle slots holds it). The **busts** (×0) and the small
pays are **interleaved** among the better slots across the whole board, never
two busts side by side, and every table returns about 95% (94.98% – 95.40%).
The higher the risk, the more of the drops bust and the bigger the top payout.
Because the centre is the likeliest slot, the top payout is a modest ×1.8 – ×4
(a bigger one there would pay back more than 100%); the surprises are the
better pays hiding between the busts out towards the edges:

| Rows | Low | Medium | High |
| --- | --- | --- | --- |
| 8 | 95.07% · ×1.8 · 11% | 95.07% · ×2.5 · 25% | 95.07% · ×3 · 44% |
| 9 | 95.27% · ×2 · 7% | 95.31% · ×2.5 · 25% | 95.21% · ×3 · 41% |
| 10 | 95.35% · ×2 · 12% | 95.14% · ×2.5 · 24% | 95.16% · ×3 · 32% |
| 11 | 95.29% · ×2 · 9% | 95.26% · ×3 · 26% | 94.98% · ×3.5 · 42% |
| 12 | 95.40% · ×2 · 14% | 95.34% · ×3 · 30% | 95.02% · ×3.5 · 39% |
| 13 | 94.98% · ×2.5 · 11% | 95.27% · ×3 · 28% | 95.24% · ×3.5 · 41% |
| 14 | 95.09% · ×2.5 · 13% | 95.33% · ×3 · 25% | 95.13% · ×3.5 · 31% |
| 15 | 94.99% · ×2.5 · 9% | 95.14% · ×3 · 29% | 95.09% · ×4 · 39% |
| 16 | 95.01% · ×2.5 · 12% | 95.26% · ×3 · 24% | 95.07% · ×4 · 42% |

(return · the top payout, in the centre · the chance of a bust; `GET
/games/api/hexfall/bets` lists every table, slot by slot, with its exact odds.)
The default — **12 rows, medium** — pays:

| Slot | Pays | Paths | Chance | Return share |
| --- | --- | --- | --- | --- |
| 1 | ×2 | 1 / 4096 | 0.02% | 0.05% |
| 2 | **bust** | 12 / 4096 | 0.29% | 0.00% |
| 3 | ×0.5 | 66 / 4096 | 1.61% | 0.81% |
| 4 | **bust** | 220 / 4096 | 5.37% | 0.00% |
| 5 | ×0.2 | 495 / 4096 | 12.08% | 2.42% |
| 6 | **bust** | 792 / 4096 | 19.34% | 0.00% |
| 7 | ×3 | 924 / 4096 | 22.56% | 67.68% |
| 8 | ×1 | 792 / 4096 | 19.34% | 19.34% |
| 9 | ×0.3 | 495 / 4096 | 12.08% | 3.63% |
| 10 | **bust** | 220 / 4096 | 5.37% | 0.00% |
| 11 | ×0.8 | 66 / 4096 | 1.61% | 1.29% |
| 12 | ×0.3 | 12 / 4096 | 0.29% | 0.09% |
| 13 | ×2.5 | 1 / 4096 | 0.02% | 0.06% |

→ return **95.34%**, house edge **4.66%**; 41.9% of drops pay the stake back or
more.

### Your own table

Set **`multipliers`** (Settings → *Custom multipliers*) to a list of **exactly
`rows + 1` numbers**, left to right, each from 0 (a bust) to 1000 — as a JSON
list or as text: `"2 0 0.5 0 0.2 0 3 1 0.3 0 0.8 0.3 2.5"` (commas, spaces and
semicolons all work; `x5`, `×5` and `bust` are understood). Empty means the
built-in table for `rows` and `risk`.

- It does not have to be symmetric. Its return and house edge are computed and
  shown like any other table's — an honest table that pays back more than 100%
  says so (and `/bets` adds a note).
- A list that does not have `rows + 1` numbers (or has something that is not a
  number in 0–1000) is **ignored**: the built-in table is used. Saving settings
  with a different `rows` drops a list that no longer fits. The panel's
  Settings card tells you before it saves; `GET /games/api/hexfall/bets` shows
  the table in use (`source`: `preset` or `custom`).
- `POST /start {"multipliers": [...]}` is the strict form: a list that does not
  fit answers **400** instead of being ignored.

### Odds are copied into the game

The table, the rows and the risk are copied into the game when it starts:
changing the settings (even the table) while a game runs never changes the odds
of that game, or of a bet already down. The next game follows the settings.

---

## The bet (all against the bank)

One bet per drop: `POST /bet {user, amount}`. The amount is debited when it is
placed; the landing credits `amount × multiplier` (rounded down) if that is at
least 1 coin — nothing on a bust. Betting again in the same window adds to the
drop's bet. A bet is for **one drop**: put coins down again for the next one.
`/remove` takes the bet back while the window is open.

### Limits

- `min_bet` / `max_bet` apply per player **per drop** (what went down in this
  window; 0 = no max).
- 500 players per game.

---

## Timeline of a game

```
POST /start ─► betting (30 s: open_bet_seconds)   rules box · "Bets close in 10!"
                 │  nothing on the drop when it closes → game over ("no_bets" / "walked")
                 ▼
               dropping (~9 s: drop_seconds)       the rune ring charges · the skull is released ·
                 │                                  one hop per row, peg flash + click ·
                 │                                  into the slot (the last two hops slow down)
                 ▼
               result (5 s)        the slot blazes · the badge · each player's payout (settled)
                 │
                 ▼
               betting (20 s: between_seconds)     the next drop … up to `drops` drops
                 │
                 ▼
               over (10 s: summary_seconds)        every drop's slot, net per player, totals
                 ▼
               idle
```

`POST /games/api/hexfall/next` (or the panel's **Skip ▶**) ends the current
phase right away — handy for testing.

**Game endings** (`outcome`): `complete` (every drop fell) · `walked` (nobody
bet on a later drop: the game ends there) · `no_bets` (nobody bet on the first
drop) · `stopped` (`/stop`) · `restart` / `error` (settled after Hexcast
stopped mid-game or a rule failed).

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
| `GET\|POST /games/api/hexfall/start` | `{drops (1–10), rows (8–16), risk (low\|medium\|high), multipliers, seconds (first window), test}` → `{started, drops, rows, risk, source, rtp_pct, state}`; **409** while a game runs; **400** for a bad `risk` or a `multipliers` list that does not have `rows + 1` numbers |
| `GET\|POST /games/api/hexfall/bet` | `{user, amount}` → `{amount, total, drop, debits:[{user, amount, seq, reason}], player}`; betting again adds to this drop's bet; **409** `bets_closed` outside a betting window |
| `GET\|POST /games/api/hexfall/remove` | `{user}` — take back what went down in **this** window → `{credits, ledger}` (a `refund`) |
| `GET\|POST /games/api/hexfall/next` | end the current phase now (alias `/drop`) → `{skipped}` |
| `GET /games/api/hexfall/table` | the STATE (below) |
| `GET /games/api/hexfall/user/{name}` | `{player, session}` — the player's bet now + session debits / credits / net |
| `GET /games/api/hexfall/ledger?since=0&limit=500` | this game's ledger events |
| `GET /games/api/hexfall/bets` | the table in use: `rows`, `risk`, `source`, `multipliers`, `rtp_pct`, `house_edge_pct`, every slot's multiplier, exact odds (`ways` of `of` paths, `probability`, `pct`) and share of the return, the three presets for the current rows, `notes`, the rules |
| `GET /games/api/hexfall/validate?amount=` | dry run of a bet: `valid`, `error`, and what the bet pays in every slot |
| `GET\|POST /games/api/hexfall/stop` | end the game now: a skull already falling counts (its slot pays), a bet still open is refunded |
| `GET /games/api/hexfall/history · last` | finished games + stats · the last one |
| `GET\|POST /games/api/hexfall/show · hide` | keep the idle scene on screen · hide it |
| `GET\|POST /games/api/hexfall/preview` | `{overrides: {appearance keys}, seconds (2–60, default 8)}` (or `x=&y=&scale=`) — the Edit Mode editor's **Test in OBS**: the scene goes on screen for `seconds` with that look on top of the saved config (`STATE.preview`), even while hidden, over whatever it shows (the idle scene between games). Never touches the game, bets or the ledger; expires on its own; `/hide`, `/stop` and `/preview/clear` end it |
| `GET · POST /games/api/config` | the `"hexfall"` section (every key in [Settings](#settings)) |

`/spin`, `/play` and `/timer` answer 400 for this game — a game starts with
`/start`, and a look is tried out on the overlay with `/preview`. So do
`/announce` and `/announce/clear`: the game-over card is built in (its numbers
are `STATE.game.summary`).

### STATE

`STATE.game` (null between games; `STATE.idle` then has the next game's table).
`STATE.preview` is `{id, overrides, expires_in_ms}` while a Test in OBS is up,
else `null`.

```jsonc
{
  "id": "hf-1a2b3c4d", "test": false,
  "phase": "dropping",              // betting | dropping | result | over
  "ends_in_ms": 4400, "phase_ms": 9000, "elapsed_ms": 4600,
  "drop": 1, "drops": 3, "rows": 12, "risk": "medium", "source": "preset",   // source: preset | custom
  "slots": [{"slot": 0, "mult": 2, "bust": false, "ways": 1, "of": 4096, "probability": 0.00024414, "pct": 0.0244, "rtp_pct": 0.0488}, "..."],
  "rtp_pct": 95.34, "house_edge_pct": 4.66,
  "fall": {"drop": 1, "path": [1,0,1,1,0,0,1,0,1,1,0,1], "slot": 7, "mult": 1, "seed": 42, "ms": 9000},   // dropping/result/over only
  "hits": [{"drop": 1, "slot": 7, "mult": 1}],          // this game's drops so far
  "recent": [{"slot": 7, "rows": 12, "mult": 1}, "..."],   // the last slot hits (newest first, across games)
  "players": [{"user": "alice", "bet": 100, "max_win": 300}],   // on the line (empty once settled)
  "on_the_line": 100,
  "last": {"drop": 1, "slot": 7, "mult": 1, "bust": false, "total_bet": 100, "total_paid": 100,
           "winners": [], "even": [{"user": "alice", "bet": 100, "paid": 100, "net": 0}], "losers": []},
  "outcome": null, "summary": null,  // set in "over"
  "currency": "coins", "min_bet": 1, "max_bet": 250
}
```

`STATE.game.summary` (phase `over`, and in `GET /history`) is the game-over
card's data: `{outcome, text, planned, rows, risk, source, rtp_pct, drops:
[{drop, slot, rows, mult, bet, paid}], total_bet, total_paid, house_net, best,
players: [{user, bet, paid, net}], test, currency}`. `best` is the game's
biggest single win, net of the bet — `{user, paid, net, drop, mult}` — or
`null` when nobody won more than they bet.

### Ledger reasons

| type | reason | when |
| --- | --- | --- |
| debit | `bet` / `add` | a bet / more on the same drop this window |
| credit | `payout` | a drop landed: bet × the slot's multiplier, rounded down (only when it is at least 1 coin; a x0.3 landing on 100 coins credits 30) |
| credit | `refund` | a bet taken back (`/remove`), or returned by `/stop` before the skull fell |

Each event's `bet_id` is `<game id>/d<drop>` (`hf-1a2b3c4d/d2`), its `roll_id`
the game's id, and its `bet` a label (`Drop 2`, `Drop 2 · ×0.5`).

The ledger is the same one roulette, craps and the other games use: one `seq`
numbering, each event names its `game`. A bank running every game tails
`GET /games/api/ledger?since=<last_seq>` once (or `&game=hexfall` for this
one) — see [The shared ledger](games.md#the-shared-ledger).

---

## Your bot's side

Hexcast never reads chat. Your bot turns chat commands into API calls and pays
from the ledger. A suggested command set (put yours in the **Commands line**
setting so the overlay shows it):

| Chat | Call |
| --- | --- |
| `!hexfall` (mod) | `POST /start` (optionally `{rows, risk}` for a spicier game) |
| `!drop 100` | `POST /bet {user, amount: 100}` |
| `!takeback` | `POST /remove {user}` |

Before a `/bet`, check the player can afford it (the debit is Hexcast's word
that the stake was taken — debit it in your bot's bank when you see it in the
ledger, or right away from the `/bet` reply's `debits`, but never both).

---

## Settings

| Key | Default | |
| --- | --- | --- |
| `drops` | 3 | drops per game (1–10) |
| `rows` | 12 | rows of pegs (8–16): `rows + 1` slots |
| `risk` | medium | `low`, `medium`, `high`: which built-in table |
| `multipliers` | [] | your own table: `rows + 1` numbers, 0–1000 (empty = the built-in table) |
| `open_bet_seconds` · `between_seconds` | 30 · 20 | betting windows |
| `drop_seconds` · `result_seconds` · `summary_seconds` | 9 · 5 · 10 | animation lengths (the fall: 6–20) |
| `min_bet` · `max_bet` | 1 · 250 | per player per drop (all of a player's bets on that drop added together); `max_bet` 0 = no max; a max below the min is dropped. Change them in the panel's Settings or `POST /games/api/config {"hexfall": {"min_bet": 10, "max_bet": 1000}}` |
| `currency` | coins | the name after amounts (your bot's coin), ≤ 24 characters |
| `commands_text` | "" | a line in the rules box, e.g. `!drop 100` |
| `drop_clip` · `win_clip` · `bust_clip` | "" | soundboard clips: the skull is released · it lands in a slot that pays the stake or more · it pays less (play on the base `/overlay`) |
| `title` | "Hexfall" | the header's name — your branding (≤ 32 characters) ✓ |
| `x` · `y` · `scale` · `theme` | 50 · 50 · 0.85 · coven | placement (0–100 % · 0–100 % · 0.2–5 × the 1280×860 scene) · `coven` (violet and green fire), `ember`, `frost` ✓ |
| `show_rules` · `show_players` · `players_max` | on · on · 8 | the rules box · the players board (on the line, then the drop's payouts) · how many it lists ✓ |
| `show_odds` · `show_history` | on · on | the payout ladder (the slots grouped by multiplier, with the return and house edge) · the strip of the last slot hits ✓ |
| `sfx` · `sfx_volume` | on · 0.6 | the overlay's own sounds ✓ |
| `hide_when_idle` | on | off = the idle scene stays on screen between games |

✓ = an appearance key: what the Edit Mode editor edits, and what a Test in OBS
(`/preview`) may override.

### Sounds

The overlay synthesizes its own sounds (WebAudio, nothing to install): a rising
charge as the ring powers up, a click and a note for every peg — a pentatonic
scale climbing as the skull falls, panned to the side it bounced — and a
different landing for a win, a big win, a small return and a bust. Turn on
**Control audio via OBS** for the browser source so they reach your mixer.

### Edit Mode & branding

Turn on **Edit Mode** (the hexbar) and click the Hexfall card (or its
**✎ Edit placement** button): the same placement & look editor as the other
games. The real scene on a 16:9 preview of the 1920×1080 stage, fed a sample
game in its bet window (six sample players, the rules box, the payout ladder),
so you place what the stream will really show. Drag it (or use the 3×3 quick
grid), **Scale** 0.2–5, **Theme**, **Title**, the rules box, payout ladder and
last-hits strip, the players board and how many it lists, sound and volume —
all live in the preview. **▶ Preview** plays a local demo drop (bets close → the
skull falls → its slot and payouts → the game-over card, about 17 s; you can
keep dragging while it plays — its path is random, it is only a demo);
**Test in OBS** puts the unsaved look on the real overlay for 8 seconds
(`/preview`); **Save** stores it and restyles every overlay; **Reset** goes back
to the defaults (not saved until Save). Cancel, ×, Esc or a click outside throw
the unsaved changes away.

`title` is text (never HTML), trimmed to 32 characters, shown upper-case in the
header, shrunk to fit and ended with "…" if it still doesn't; empty means the
default. A plain emoji counts as one character; a flag, a skin-tone emoji or a
combined one (a family) counts as several, and one that doesn't fit whole is
dropped, never cut in half. Set it in Edit Mode (to see it live) or in the
tab's Settings card.

---

## Storage & restarts

`config/games_hexfall.json` holds the running game (and the last 50 finished
ones + stats). Every change is written before its ledger events, with those
events as a journal, so a crash can't pay twice or lose a stake. A game still
running when Hexcast stops is **settled at the next start-up**: a skull already
falling pays its slot, then a bet still open is refunded. Removing the add-on
settles a running game the same way (`/stop`) and keeps the settings and the
file, so putting it back brings everything back.
