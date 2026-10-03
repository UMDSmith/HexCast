# Games — bot-driven overlay games (Roulette)

> **A plugin with add-ons.** This page documents the Games plugin and its first game, **Roulette** (the `games_roulette`
> add-on). Craps, Russian Roulette, Trivia, Hexfall, Soul Climb and Blackjack are add-ons too - see [Craps](craps.md),
> [Russian Roulette](russian_roulette.md), [Trivia](trivia.md), [Hexfall](hexfall.md), [Soul Climb](climb.md) and
> [Blackjack](blackjack.md) - and all of them are installed from the **+** inside
> the Games tab. Only the games you install have a tab, an overlay layer and API routes.

A home for games your chat — or a bot — can play on stream. The first one is
**Roulette**: trigger a spin from the panel or with one HTTP call and a casino
wheel pops onto the stream. The wheel spins, the ball is launched the other
way, slows, drops off the track, bounces off the deflectors and frets, and
settles in a pocket; then the result pops up. Bets are optional: send them
with the spin and every one comes back resolved at standard roulette odds,
ready for your bot to pay out. Or put chat's bets on the **table** ahead of
the spin, like craps: a **spin timer** counts down, the wheel spins by itself
at zero, and every coin movement goes into the numbered **ledger** your
bot's bank reads (see [Table & spin timer](#table--spin-timer)). Or keep the
bets in your bot, do the math there, and tell the overlay who won with one
more call (see [Two ways to run the money](#two-ways-to-run-the-money)).

The **server** picks the pocket; the overlay only animates it. Everything
lives under `/games/*`.

---

## Games in this module

| Game | What it is | Docs |
| --- | --- | --- |
| **Roulette** | an American double-zero wheel; bets sent with a spin come back resolved for your bot to pay, or chat's bets wait on a table for the next spin, with a spin timer and a ledger that Hex pays from | this page |
| **Craps** | a bank-craps table: chat bets hexcoins that stay on the table across rolls, with a ledger that Hex, the channel's bot, pays from | [docs/craps.md](craps.md) |
| **Russian Roulette** | a revolver and a stuffed dummy: pull k loads k bullets; chat bets it survives (and rides) or goes bang, against the bank; a volunteer's name on the dummy earns a cut | [docs/russian_roulette.md](russian_roulette.md) |
| **Trivia** | a game-show quiz (Open Trivia DB + your own lore questions): bet before the question, answer A–E, ride winnings for a streak bonus or cash out | [docs/trivia.md](trivia.md) |
| **Hexfall** | a hex-themed plinko: a token drops through hexagonal pegs into multiplier and bust slots; chat puts up a bet before every drop | [docs/hexfall.md](hexfall.md) |
| **Soul Climb** | a damned soul climbs out of a hell pit; chat bets how high he gets and is paid that height's multiplier if he reaches it | [docs/climb.md](climb.md) |
| **Blackjack** | Vegas blackjack with Hex as the dealer: up to 14 seats, a shoe that grows with the player count, everyone acting at once, chip stacks at every seat | [docs/blackjack.md](blackjack.md) |

They share one panel at `/games` — **one tab per game** under the top bar
(`/games#roulette`, `/games#craps`, `/games#russian`, `/games#trivia`, `/games#hexfall`, `/games#climb`, `/games#blackjack` open
straight to one) — one OBS browser source (`/games/overlay` shows every game;
`?game=roulette`, `?game=craps`, `?game=russian`, `?game=trivia`, `?game=hexfall`, `?game=climb` or `?game=blackjack` limits a
source to one), the same Edit Mode, the same `/games/api/{game}/...` API shape,
and one [ledger](#the-shared-ledger). Russian Roulette, Trivia, Hexfall, Soul Climb and Blackjack are *round
games* — one multi-round game at a time, started with `/start` instead of a
spin (their `/spin` and `/timer` answer 400). They have no test spin to carry
the Edit Mode editor's **Test in OBS**, so theirs is `POST /games/api/{game}/preview`
(the look on the overlay for a few seconds, `STATE.preview`), and their titles
are settings (`title`, trivia's `lore_label`) so a stream can brand them; their
pages have the details. The rest of this page is about Roulette and the parts
every game shares.

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

Games is a plugin, and each game is an **add-on** of it, so you install only what you play:

1. Open the **+** tab in the top bar, find **Games** and press **Install**. A **Games** tab appears.
2. In the Games tab, click the **+** at the end of its own tab strip. Pick a game - **Roulette**, **Craps**,
   **Russian Roulette**, **Trivia**, **Hexfall**, **Soul Climb**, **Blackjack** - and press **Install**. Its tab appears at once.

From a terminal: `python hexcast.py plugins install games games_roulette games_craps games_russian games_trivia games_hexfall games_climb games_blackjack`
(add only the games you want). There are no extra Python packages.

Removing a game keeps its settings (`config/games.json`) and its table file, so putting it back brings everything
back - including bets that were still down. Take bets off the table first if you don't want them to wait.

---

## Browser source

| Source | URL |
| --- | --- |
| Games | `http://localhost:4747/games/overlay` |
| Roulette only | `http://localhost:4747/games/overlay?game=roulette` |

Add it as a **1920×1080** Browser source (match your canvas) — the page is
transparent and the wheel sits on a 1920×1080 stage that scales to whatever
size you give the source, so everything keeps its proportions. The plain URL
shows every game; `?game=roulette` gives roulette its own source (and its own
layer in OBS).

- Uncheck **Shutdown source when not visible** (otherwise the websocket dies)
  and **Refresh browser when scene becomes active**.
- Turn **Control audio via OBS** on. The built-in ball sounds — the rolling
  rumble, fret clicks and the landing clack — are synthesized inside the
  overlay, so this is how they reach your mixer.
- **Launch/landing clips** (see [Soundboard clip cues](#soundboard-clip-cues))
  play on the soundboard overlay (`/overlay`), not this one — keep both
  browser sources in the scene.

The **OBS browser source** card on the panel shows both URLs (all games, and
this game only) for the machine you're on, each with a **Copy** button.

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
4. If the spin carried bets, or bets were riding on the
   [table](#table--spin-timer), and **show bets** is on, a **winners list**
   appears: both kinds together, biggest win first (up to **bets max**
   entries). When a bot that does its own math posts a winners card with
   [`/announce`](#announce), that card shows here instead, in the same style.
5. The **history strip** under the wheel (last **history count** results)
   updates only *after* the ball lands — it never gives away the current spin.
6. After **result seconds** the wheel fades out (with **hide when idle** on);
   with it off, the wheel stays on screen, idling.

**Before the spin**, while bets are on the [table](#table--spin-timer) and
**show table** is on, an **ON THE TABLE** board sits beside the wheel: one
line per bet (`@alice  Red  150 hexcoins`), biggest first, up to **table
lines** then `+N more`, and the total. **Table position** puts it right,
left, above or below the wheel (it moves to the other side if it would run
off the canvas). When a bot posts its own [board](#board), that shows
instead. While a [countdown](#the-countdown-auto-spin-and-the-spin-timer)
runs, a **NEXT SPIN 0:12** pill with a draining ring sits above the wheel
(even with the board off) and the board's title reads **PLACE YOUR BETS** (a
bot's board keeps its own title); in the last five seconds the pill turns
red and throbs, and at zero it says **NO MORE BETS** until the ball is
thrown. The board and the countdown only show between spins, never while the
ball is in the air or the result is up, so they never give a spin away.

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

Each game's tab shows that add-on's version at the right end of the tab row (`Craps Version: 1.0`), and a green
**Update to 1.1** button when a newer version of that game exists (from `catalog/` or GitHub - see
[Versions and updates](plugins.md#versions-and-updates)); pressing it updates only that game and keeps its settings. The
game store (the **+** tab) shows each game's version on its card, and `GET /games/api/registry` carries `version`,
`version_label`, `latest_version`, `latest_label`, `update_available` and `update_source` for every game.

Each game has its own **tab** under the top bar (**Roulette** and
[**Craps**](craps.md#the-panel)).
Everything below lives on the Roulette tab; `/games#roulette` opens straight
to it.

- **OBS browser source** — the all-games overlay URL and the
  `?game=roulette` one for this host, each with **Copy**.
- **Roulette** — a live mirror: a 16:9 preview holding a scaled 1920×1080
  stage with the real wheel (sound off), playing the same spins as the
  overlay, with the board and the countdown before a spin. **Spin** starts a
  spin, **Show** / **Hide** put the idle wheel on or off screen. A readout
  shows the state (`idle`, `betting` while a countdown runs, `spinning`,
  `result`, `cooldown`) with a countdown, then three facts — **On the table**
  (the coins riding on the next spin), **Spin timer** (`in 0:12`; `armed` or
  `waiting` with auto-spin on and no countdown yet; `off`) and **Bets**
  (`open`, or `closed` while the ball is in the air) — and a chip with the
  last result. **Start timer** (the seconds box is optional; blank uses the
  bet window) and **Cancel timer** run the
  [spin timer](#the-countdown-auto-spin-and-the-spin-timer).
- **On the table** — every bet riding on the next spin, grouped by viewer:
  bet, amount and what it pays. **Take down** on a bet (with a confirm)
  refunds it; a viewer with two or more bets also gets **Take down all**.
  **Refund all & clear table** (with a confirm) refunds every bet. The
  buttons are greyed out while the ball is in the air.
- **Place a bet** — type a bet string, an amount and a user. **Check** is a
  dry run: how the bet parses (type, odds and covered numbers, or the error).
  **Place bet** is **real**: the bet goes on the table and a debit goes in the
  ledger, so Hex's ledger tail takes those coins from that viewer. **Spin
  with this bet** sends it along with a spin instead (a
  [bet on the spin call](#bets): not on the table, not in the ledger).
- **Hex display** — try the [Mode B display calls](#two-ways-to-run-the-money)
  by hand. For the [announce](#announce) card: a title, the lines (one per
  line, `user amount text…`, e.g. `alice 200 Split 17/20`; the amount is
  optional, and `-` as the user means none), the empty text, seconds and an
  optional spin id, then **Announce** or **Clear**. For the [board](#board):
  a title and a box of bets (`user amount text…` per line) with **Set board**
  and **Clear board**. Blank fields use the server's defaults. It shows the
  request and the reply; the mirror above shows the card and the board,
  exactly like the overlay.
- **Ledger** — a live tail of roulette's coin movements (the
  [table](#table--spin-timer)'s bets, wins, take-downs and refunds): seq,
  time, user, debit/credit, amount, reason and bet, with coins in, coins out,
  what's on the table and the house net, per-user nets, **Filter by user**
  (**Everyone** resets it) and **Reload**. Seq gaps are craps events — the
  ledger is shared.
- **History & stats** — the last 20 results as coloured chips (newest
  first), red/black/green, odd/even and low/high counts, hot and cold numbers,
  the current streak, and **Clear history**.
- **Settings** — spin seconds, result seconds, cooldown, the bet window,
  currency name, min and max bet, the soundboard clips for launch and
  landing (suggestions come from your soundboard library), bets shown, table
  lines and the table board's position, the built-in sounds with their
  volume, and the switches: hide the wheel when idle, keep it on screen while
  bets are down, auto-spin, the "on the table" board, the `@user spins`
  caption and the winners list. **Save settings** stores them; **Revert**
  drops your changes.
- **API** — a compact endpoint table with curl examples built for this host
  (the table, timer, ledger, board and announce endpoints included), and the
  table route's steps for the bank.

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
  result details, show result, show history, and history count; and the
  **Board**: the "on the table" board on or off, its position (right / left /
  below / above the wheel) and how many lines it lists. The preview holds a
  sample table — a few bets and a countdown standing still, greyed out — so
  the board and the countdown can be placed before any real bet is down.

The buttons along the bottom:

- **▶ Preview** — a local demo spin inside the editor (random pocket, your
  configured spin time), with the sample bets settled on it. Nothing is sent
  to the server or the stream.
- **Test in OBS** — a real `test` spin on the overlay using the editor's
  *unsaved* placement and appearance, so you can see it on stream before
  committing. Test spins stay out of history and stats, don't fire clip
  cues and never touch the table's bets — but like any spin they stop a
  running [countdown](#the-countdown-auto-spin-and-the-spin-timer). If the
  wheel is busy you'll get a toast instead.
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
  │                              │                                │                            │
  spin_clip fires                land_clip fires                  wheel hides                  auto-spin countdown
  table bets close               spin committed to history        (if hide when idle           restarts (if bets
                                 table bets settle, winners       and nothing keeps it up)     are still down)
                                 credited in the ledger,
                                 bets open again
```

- At **launch** the server has already picked the pocket; the API response
  (unless `wait=true`) comes back immediately with the result in it. The
  [table](#table--spin-timer) is frozen until the landing: new table bets,
  take-downs and clears get **409** `bets_closed`. (A `test` spin doesn't
  freeze anything.)
- At **landing** the spin is committed to history and stats (unless it's a
  test), `land_clip` fires, and the overlay shows the result. The table's
  bets are settled and come down, and the winners' credits go into the
  ledger. Table bets are accepted again straight away — during the result
  and cooldown too; they ride on the next spin.
- **Result seconds** later the result phase ends — the wheel hides if **hide
  when idle** is on — then the optional **cooldown** runs.
- While any of that is going on (`spinning`, `result` or `cooldown`) new spins
  are refused with **409 busy**, and so is a new [timer](#timer). With the
  defaults (9 s spin, 6 s result, no cooldown) that's one spin every 15
  seconds at most.

---

## Two ways to run the money

Hexcast never holds anyone's coins or points — your bot does. On this
channel that's **Hex**, a bot whose own code keeps everyone's hexcoins in its
**hexbank**. The choice is **who does the bet math**:

| | **Mode A — Hexcast does the math** | **Mode B — Hex does the math** |
| --- | --- | --- |
| The bets live in | the spin call (`bets`), **or** Hexcast's [table](#table--spin-timer) (`/bet`) until the next spin | Hex's own code |
| Payouts are worked out by | Hexcast, at standard odds (`win`, `payout`, `returned`; `settlements` for the table) | Hex's own code, from the spin's `result` |
| Coins are moved by | Hex's hexbank: each bet's `returned` (spin call), or the `/bet` reply's `debits` and then the [ledger](#the-shared-ledger)'s credits (table) | Hex's hexbank |
| The wheel spins on | a `/spin` call, or at the end of the [spin timer](#the-countdown-auto-spin-and-the-spin-timer) | a `/spin` call, or at the end of the spin timer |
| The board before the spin | the table's bets | the board Hex posts with [`/board`](#board) |
| The winners on screen | the winners list Hexcast builds from the bets | the card Hex posts with [`/announce`](#announce) |
| The pocket is picked by | **Hexcast's fair spin** | **Hexcast's fair spin** |

The last row is the same either way. Hex can't send a result — there's no
parameter for one — so in Mode B Hex only tells the overlay what to
**show**. The display calls never touch the result, the table's bets, the
ledger, the history or the stats.

**Pick Mode A** when standard roulette is what you want — no odds tables in
Hex, and the overlay's winners list comes for free. Mode A has two routes:

- **Bets on the spin call.** Hex collects a round, sends every bet with the
  spin and credits each bet's `returned` from the reply. Nothing is stored
  and nothing goes in the ledger. See [Bets](#bets),
  [A chat betting round](#a-chat-betting-round) and, for Hex's hexbank,
  [Hooking up the bank](#hooking-up-the-bank-hexcoins).
- **The table and the spin timer.** Chat's bets go on Hexcast's table with
  `/bet` (Hex takes the coins in the reply's `debits`), a countdown spins the
  wheel, and every payout comes through the ledger — saved to disk, paid
  exactly once, whoever spun and whichever side restarts. This is craps' way,
  and the durable one. See [Table & spin timer](#table--spin-timer).

The two routes can share a spin — bets on the table and bets on the spin
call are settled side by side — but pay each bet from its own route only:
`returned` for a bet sent with the spin, the ledger for a bet on the table.
Never also send a table bet in a spin's `bets`, and never pay one bet from
both.

**Pick Mode B** when Hex already has betting code of its own, or wants rules
Hexcast doesn't have — house odds, side bets, a jackpot, limits per viewer.
Hex keeps the bets, spins **without** `bets` (or lets the spin timer spin),
works out the payouts from `result`, pays from its hexbank and posts the
winners with `/announce`, and can mirror its bets on the board before the
spin with `/board`. See [Hex does the math (Mode B)](#hex-does-the-math-mode-b)
and, with the timer, [Hex's side: the spin timer only](#hexs-side-the-spin-timer-only-mode-b).

Use one mode per spin. If a spin settles bets — sent with it or on the
table — **and** Hex posts an `/announce`, the card replaces the winners list
on screen, but those bets are still settled: the reply's `returned` and the
ledger's credits still stand. Pay each bet once, by Hexcast's math or by
Hex's, never both.

Craps has the same two modes, the same kind of table, ledger and timer, and
the same board — see
[Craps → Two ways to run the money](craps.md#two-ways-to-run-the-money).

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

Test any string with **Check** on the panel's **Place a bet** card or
`GET /games/api/roulette/validate?bet=...` before you trust it — see
[Validate](#validate-and-the-bet-reference).

The same bet strings work on the [table](#bet).

### How bets are resolved

Every bet you send **with a spin** comes back with extra fields (bets on the
table are checked when they're placed and come back as
[`settlements`](#settling-a-spin)):

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
  `returned: amount` — the stake refunded. The exception is a bad `amount`
  (negative, not a number, over `1e12`): Hexcast couldn't read a stake, so
  the bet comes back with `amount: 0` and `returned: 0`. `/validate` only
  checks the bet text, so checking the amount is the bot's job.
- Hexcast keeps **no balances or points**. If your bot takes the stake when
  the bet is placed, crediting every bet's `returned` after the spin is all it
  needs: winners get stake + winnings, invalid bets get a refund, losers get
  nothing.

---

## Table & spin timer

The other Mode A route, and the way [craps](craps.md) works: chat's bets go
on Hexcast's **table** ahead of the spin, a **countdown** runs, and at zero
the wheel spins by itself with every bet on the table riding on it. Hexcast
settles them at standard odds and writes every coin movement to the
numbered, persisted [ledger](#the-shared-ledger) — the same ledger craps
uses — so Hex, the bank, pays each win exactly once, whoever spun and
whichever side restarts.

- **Placing a bet** — [`/bet`](#bet) checks it (the bet text, whole coins,
  `min_bet`, `max_bet`) and answers with the exact coins to take, in
  `debits[]`. A rejected bet moves nothing.
- **Spinning** — the [countdown](#the-countdown-auto-spin-and-the-spin-timer)
  starts on its own with the first bet when **auto-spin** is on, or when Hex
  (or the panel's **Start timer**) calls [`/timer`](#timer). A `/spin` from
  anywhere spins the table's bets too.
- **Paying out** — at the landing each winning bet gets one ledger credit:
  stake + winnings. Hex applies credits by tailing the ledger; the spin reply
  and the winners list on screen are for announcing.
- **Taking bets down** — every roulette bet is a one-spin bet, and any of
  them can come down (a refund) until the ball is thrown.

Table bets are closed only while the ball is in the air (**409**
`bets_closed`); during the result and cooldown they're open and ride on the
next spin. The pocket is still [Hexcast's fair spin](#fairness), and `/spin`
works exactly as before, bets on the spin call and all.

### Settings

The table's keys in the `"roulette"` section of the config (they're also in
[Config keys](#config-keys)):

| Key | Default | Values |
| --- | --- | --- |
| `currency` | `"hexcoins"` | 1–24 characters — the name on the board, the winners list and in the table; also the [announce](#announce) card's default |
| `min_bet` | `1` | whole number, 1–1 000 000 000 — the least one `/bet` entry may put down |
| `max_bet` | `100000` | whole number, 0–1 000 000 000 000; `0` = no maximum — the most one bet line may hold, after adding to it. A value below `min_bet` is reset to the default (or to `0` if `min_bet` is above that) |
| `auto_spin` | `false` | spin by itself when the bet window runs out (see [the countdown](#the-countdown-auto-spin-and-the-spin-timer)) |
| `bet_window_seconds` | `20` | 5–300 — the auto-spin countdown, and `/timer`'s default |
| `show_when_bets` | `true` | keep the wheel on screen while bets are on the table, or Hex's [board](#board) has lines (even with hide when idle) |
| `show_table` | `true` | the "on the table" board before the spin |
| `table_max` | `6` | 1–20 — board lines before `+N more` |
| `table_position` | `"right"` | `right` \| `left` \| `below` \| `above` — where the board sits next to the wheel |
| `show_rules` | `true` | the "how to play" box while bets are open (no spin up): the bets in one line each and what they pay, in the theme's colours. It takes the side the board doesn't |

`currency`, `min_bet` and `max_bet` only apply to the table — bets sent with
a spin call are unchanged. `show_table`, `table_max` and `table_position` are
appearance keys: per spin in `overrides`, and in the Edit Mode editor.

```
curl -X POST http://localhost:4747/games/api/config -H "Content-Type: application/json" -d "{\"roulette\":{\"auto_spin\":true,\"bet_window_seconds\":30,\"min_bet\":10}}"
```

### Bet

`GET` or `POST /games/api/roulette/bet`. One bet as `user`, `bet`, `amount`,
or a list as `{"bets": [...]}` (up to 200 — more is a **400** and nothing is
placed); a top-level `user` is the default for entries without one.
Parameters go in the query string, a JSON body (POST), or both — the body
wins.

```
curl "http://localhost:4747/games/api/roulette/bet?user=alice&bet=red&amount=100"
curl -X POST http://localhost:4747/games/api/roulette/bet -H "Content-Type: application/json" -d "{\"bets\":[{\"user\":\"bob\",\"bet\":\"split:17/20\",\"amount\":25},{\"user\":\"carol\",\"bet\":\"17\",\"amount\":10}]}"
```

The same [bet strings](#bet-reference) as a spin's; URL-encode them in a
query string if your bot's HTTP tool doesn't. The second call answers:

```json
{
  "ok": true,
  "accepted": [
    {"id": "rb-5e6f7a8b", "user": "bob", "bet": "split:17/20", "label": "Split 17/20", "type": "split",
     "numbers": ["17", "20"], "odds": 17, "amount": 25, "pays": 450, "placed_at": 1790000000.1,
     "removable": true, "action": "new", "added": 25},
    {"id": "rb-9c0d1e2f", "user": "carol", "bet": "17", "label": "Straight 17", "type": "straight",
     "numbers": ["17"], "odds": 35, "amount": 10, "pays": 360, "placed_at": 1790000000.1,
     "removable": true, "action": "new", "added": 10}
  ],
  "rejected": [],
  "debits": [{"user": "bob", "amount": 25, "bet_id": "rb-5e6f7a8b", "seq": 43, "reason": "bet"},
             {"user": "carol", "amount": 10, "bet_id": "rb-9c0d1e2f", "seq": 44, "reason": "bet"}],
  "table": {"...": "..."}
}
```

- `accepted` — each accepted bet as it now is on the table (a [BET](#table)),
  plus `action` and `added`. `action` is `new`, or `add` when that user
  already had the same bet down — the same type and numbers, however it was
  written (`20/17` adds to `split:17/20`) — and this amount went on top of
  it; `added` is what this entry added. `amount` and `pays` are the line's
  new totals.
- `rejected` — `{user, bet, amount, error}` for each bet that wasn't taken.
  `error` is written for chat: `minimum bet is 10 hexcoins`, `max bet is
  100000 hexcoins (60000 already on Red)`, `amounts are whole coins`,
  `amount must be positive`, `user required`, or what's wrong with the bet
  text (`18 and 19 are not next to each other on the table`). Bet text that
  didn't parse also gets a `hint` with the forms to try. **Nothing is debited
  for a rejected bet.**
- `debits` — one per accepted entry: the coins Hex takes now, with the
  ledger `seq` and `reason` (`bet` for a new line, `add` for more on one).
- HTTP **200** when anything was accepted (or nothing was sent), **400** when
  every bet was rejected. The body's `error` then holds the reason (or
  `every bet was rejected` when there were several).
- HTTP **409** `{"ok": false, "error": "bets_closed", "retry_in_ms": N}` while
  the ball is in the air — nothing was placed; try again after `retry_in_ms`.

Amounts are whole coins: at least `min_bet`, and a bet line — after adding
to it — at most `max_bet`. The odds are whole numbers, so every payout is
whole too; nothing is rounded. The table holds up to 1000 bet lines; past
that a new line is rejected (`the table is full (1000 bets) - …`), while
adding to a line already down still works. The `user` is cleaned as
everywhere (a leading `@` dropped, up to 40 characters) but keeps its case,
so always send the same form of a name — the viewer's lowercase login is a
good choice — or one viewer's bets won't group together and a ledger event's
`user` won't match the hexbank's.

### Table

`GET /games/api/roulette/table` → `{"ok": true, "table": TABLE}`. The same
`table` rides along in the `/bet`, `/remove`, `/clear`, `/board`, `/timer`
and `/spin` replies and in every roulette [STATE](#websocket-protocol).

```json
{
  "bets": [
    {"id": "rb-5e6f7a8b", "user": "bob", "bet": "split:17/20", "label": "Split 17/20", "type": "split",
     "numbers": ["17", "20"], "odds": 17, "amount": 25, "pays": 450, "placed_at": 1790000000.1,
     "removable": true}
  ],
  "exposure": {"bob": 25}, "total_on_table": 25,
  "bets_open": true, "auto_spin_in_ms": 12340, "last_seq": 43, "currency": "hexcoins",
  "min_bet": 1, "max_bet": 100000, "display_board": null
}
```

| Field | Meaning |
| --- | --- |
| `bets` | every bet riding on the next spin, oldest first |
| `exposure` | coins each user has on the table |
| `total_on_table` | all of it added up |
| `bets_open` | `false` only while the ball is in the air |
| `auto_spin_in_ms` | time left on the [countdown](#the-countdown-auto-spin-and-the-spin-timer) (auto-spin or `/timer`), measured when the reply was built; `null` when none is running |
| `last_seq` | the newest ledger `seq` — of any game, since the [ledger](#the-shared-ledger) is shared |
| `currency`, `min_bet`, `max_bet` | from the config |
| `display_board` | Mode B: the board Hex posted with [`/board`](#board), `{"title", "bets", "total"}`. `null` when there isn't one — the overlay's board then shows `bets` |

A **BET** on the table:

| Field | Meaning |
| --- | --- |
| `id` | `rb-…`, stable while the bet is on the table |
| `user` | whose it is |
| `bet` | the bet text as it was first placed |
| `label` | readable name for chat (`Split 17/20`) |
| `type`, `numbers`, `odds` | as in [How bets are resolved](#how-bets-are-resolved) — match on these in code |
| `amount` | the stake, whole coins |
| `pays` | what a win brings back: `amount × (odds + 1)`, stake included |
| `placed_at` | server epoch seconds, when the line was first placed |
| `removable` | always `true`: any roulette bet can come down until the ball is thrown |

The table is saved to disk after every change and restored when Hexcast
starts, so bets survive a restart. `display_board` isn't saved: it's `null`
again after a restart.

### Remove and clear

`GET` or `POST /games/api/roulette/remove` — take bets down. The coins come
back as credits (reason `remove`).

| Body / query | Takes down |
| --- | --- |
| `{"bet_id": "rb-5e6f7a8b"}` | that bet (with a `user` too, only if it's theirs) |
| `{"user": "bob", "bet": "split:17/20"}` | that user's bet, named with any spelling of its bet text |
| `{"user": "bob", "all": true}` | everything that user has on the table |

```
curl "http://localhost:4747/games/api/roulette/remove?user=bob&bet=split:17/20"
```

Response: `{"ok": true, "removed": [BET, ...], "credits": [{"user", "amount"}], "ledger": [...], "table": {...}}`
— each removed entry also has `refund` (the coins back). When nothing
matches it's **400** with an `error` saying why (`@bob has no Split 17/20
bet on the table`, `no bet with id rb-5e6f7a8b on the table`,
`bet rb-5e6f7a8b belongs to @bob`); while the ball is in the air it's
**409** `bets_closed`. Taking the last bet down stops an auto-spin
countdown (not a [`/timer`](#timer) one).

`GET` or `POST /games/api/roulette/clear` refunds every bet on the table
(credits, reason `refund`) and stops any countdown. It returns
`{"ok": true, "credits": [...], "ledger": [...], "refunded": N, "table": {...}}`,
or **409** `bets_closed` while the ball is in the air. The panel's **Refund
all & clear table** does the same.

```
curl http://localhost:4747/games/api/roulette/clear
```

### User

`GET /games/api/roulette/user/{name}` →

```
{"ok": true, "user": "alice", "bets": [BET], "exposure": 100,
 "session": {"debits": 250, "credits": 300, "net": 50, "events": 4}}
```

(alice put 100 on red and 50 more, which won 300; then 100 on red again.)
`bets` and `exposure` are what that user has on the table now. `session`
adds up their roulette events in the ledger Hexcast keeps in memory (the
latest 10 000, of every game): `debits`, `credits`, `net` = credits − debits
(coins on the table count as spent until they come back), and the event
count. Handy for a `!mybets` reply. It isn't a balance; Hex's hexbank owns
balances.

### The countdown: auto-spin and the spin timer

Two kinds of countdown lead up to a spin. Either way, at zero the server
spins exactly like a `/spin` with no parameters — no `user`, the configured
**spin seconds** — and every bet on the table rides on it.

| | **Auto-spin** (`auto_spin: true`) | **Spin timer** ([`/timer`](#timer), the panel's **Start timer**) |
| --- | --- | --- |
| Starts | on its own while bets are on the table and the wheel is idle: with the first bet, again once a spin's result (and cooldown) is over if bets are still down, and when auto-spin is switched on with bets down | when it's called — with or without bets, auto-spin on or off. Called while a countdown runs, it starts over |
| Length | `bet_window_seconds` (default 20) | `seconds` (5–300), default `bet_window_seconds` |
| At zero | spins, if auto-spin is still on and bets are still down | always spins, even with nothing on the table |
| Stopped by | any spin (a test spin too), `/stop`, `/timer/cancel`, `/clear`, taking the last bet down, switching auto-spin off | any spin (a test spin too), `/stop`, `/timer/cancel`, `/clear` |

- `table.auto_spin_in_ms` is the time left on either kind, measured when the
  reply or message was built, and `null` when nothing is counting. The
  overlay shows it as **NEXT SPIN 0:12**, the panel as **Spin timer**.
- Nobody's HTTP call waits on a spin the countdown starts. Hex learns about
  it from the ledger (the winners' credits) and from
  `GET /games/api/roulette/last`, the latest landed spin (or the websocket's
  `state` messages).
- A `test` spin (the editor's **Test in OBS**) stops the countdown too.
  Auto-spin starts over from the full bet window once the test spin is over,
  if bets are still down; a spin timer is simply gone — start it again.
- A countdown doesn't survive a Hexcast restart. The bets do; auto-spin
  counts again from the next bet placed, or start the timer.
- **On screen.** While a countdown runs, the wheel stays on screen — even
  with **hide when idle** on and nothing on the table — unless `/hide` or
  **⏹ Stop** took it off (after a `/hide` the countdown keeps running, and
  the spin at zero brings the wheel back). `/timer` brings a hidden wheel
  back, the way a spin does. With **show when bets** on, the wheel also stays
  up while bets are on the table or Hex's [board](#board) has lines.
- Auto-spin only counts bets on Hexcast's table, not a board Hex posted — in
  [Mode B](#hexs-side-the-spin-timer-only-mode-b) it never starts, and Hex
  starts each countdown with `/timer`.

### Timer

`GET` or `POST /games/api/roulette/timer` starts the spin timer now, or
starts it over. `seconds` goes in the query string or the JSON body; left
out or not a number, it's the **bet window** (`bet_window_seconds`), and
it's clamped to 5–300.

```
curl "http://localhost:4747/games/api/roulette/timer?seconds=30"
curl -X POST http://localhost:4747/games/api/roulette/timer -H "Content-Type: application/json" -d "{\"seconds\":45}"
curl http://localhost:4747/games/api/roulette/timer/cancel
```

```json
{"ok": true, "auto_in_ms": 30000, "table": {"auto_spin_in_ms": 30000, "...": "..."},
 "state": {"state": "idle", "visible": true, "...": "..."}}
```

- `auto_in_ms` — the countdown's length as it started (the table's
  `auto_spin_in_ms` from then on).
- While a spin is spinning, showing its result or in cooldown, it's **409**
  busy — the same body as a busy [`/spin`](#busy). Nothing started; try
  again after `retry_in_ms`.
- `GET|POST /games/api/roulette/timer/cancel` stops any countdown, auto-spin's
  or the timer's — nothing spins — and answers
  `{"ok": true, "cancelled": true, "table": {...}, "state": {...}}`
  (`cancelled` is `false` when none was running). With auto-spin on, the
  next bet placed starts a fresh one.
- Both are generic routes: `/games/api/craps/timer` and `/timer/cancel` do
  the same for craps (see [Craps → Roll timer](craps.md#roll-timer)).

### Settling a spin

When the ball is thrown — by `/spin`, the panel or a countdown — the pocket
is already picked, and every bet on the table is worked out against it.
Next to the bets sent with the spin (`bets` and `summary`, unchanged), the
spin object carries the table's side. Here it's the reply to a `/spin` with
`wait=true` and no bets of its own; a countdown's spin carries the same
fields in `/last`, `/history` and the [STATE](#websocket-protocol):

```json
{
  "ok": true,
  "id": "r-8ca506f5",
  "game": "roulette",
  "result": {"number": "3", "color": "red", "...": "..."},
  "user": "hexmod",
  "test": false,
  "lands_at": 1790000009.123,
  "...": "...",
  "bets": [],
  "summary": {"bets": 0, "winners": 0, "invalid": 0, "total_wagered": 0, "total_payout": 0},
  "settlements": [
    {"bet_id": "rb-1a2b3c4d", "user": "alice", "bet": "red", "label": "Red", "type": "red",
     "amount": 150, "odds": 1, "win": true, "payout": 150, "credit": 300},
    {"bet_id": "rb-9c0d1e2f", "user": "carol", "bet": "17", "label": "Straight 17", "type": "straight",
     "amount": 10, "odds": 35, "win": false, "payout": -10, "credit": 0}
  ],
  "credits": [{"user": "alice", "amount": 300}],
  "table_summary": {"bets": 2, "wagered": 160, "paid": 300, "net": -140},
  "landed": true,
  "table": {"bets": [], "...": "..."},
  "ledger": [{"seq": 47, "game": "roulette", "type": "credit", "user": "alice", "amount": 300,
              "reason": "win", "bet_id": "rb-1a2b3c4d", "bet": "Red", "roll_id": "r-8ca506f5", "...": "..."}]
}
```

- `settlements` — one per bet on the table, in table order: `win`,
  `payout` (the net: `amount × odds` for a win, `-amount` for a loss) and
  `credit` (the coins paid back at the landing: `amount × (odds + 1)` for a
  win — stake + winnings — else `0`).
- `credits` — the same coins added up per user, winners only, for the chat
  announcement.
- `table_summary` — the house's view: `bets`, `wagered`, `paid` and `net` =
  wagered − paid (negative when the players are up).
- `table` — with `wait=true`, the table after the landing (the settled bets
  gone). Without `wait`, the table as the ball was thrown
  (`bets_open: false`).
- `ledger` — with `wait=true`, the ledger events this spin wrote (its `win`
  credits).

**Settlements are worked out at the throw but committed at the landing.**
Then every settled bet comes down, and each winning bet gets one ledger
credit — reason `win`, the full `credit`, with `roll_id` set to the spin's
`id`. A losing bet gets no event: its stake was taken when it was placed. A
**⏹ Stop** while the ball is in the air commits the spin just the same. A
Hexcast **restart** while it's in the air cancels the spin: nothing is
settled, and the bets ride on the next one. A `test` spin never touches the
table (`settlements: []`; the bets stay for the next real spin). So don't
pay from a spin's reply — pay from the ledger.

### The shared ledger

Every game shares one ledger — roulette, craps, Russian Roulette, Trivia, Hexfall, Soul Climb and Blackjack:
one file, `config/games_ledger.jsonl`, one `seq` numbering, and every event
names its game in `game`. A bank running every game tails
`GET /games/api/ledger?since=<last_seq>` once.

| Endpoint | Events |
| --- | --- |
| `GET /games/api/roulette/ledger?since=0&limit=500` | roulette's |
| `GET /games/api/craps/ledger?since=0&limit=500` | craps' (see [Craps → Ledger](craps.md#ledger)) |
| `GET /games/api/russian/ledger?since=0&limit=500` | Russian Roulette's (see [its ledger reasons](russian_roulette.md#ledger-reasons)) |
| `GET /games/api/trivia/ledger?since=0&limit=500` | Trivia's (see [its ledger reasons](trivia.md#ledger-reasons)) |
| `GET /games/api/hexfall/ledger?since=0&limit=500` | Hexfall's (see [hexfall.md](hexfall.md)) |
| `GET /games/api/climb/ledger?since=0&limit=500` | Soul Climb's (see [climb.md](climb.md)) |
| `GET /games/api/blackjack/ledger?since=0&limit=500` | Blackjack's (see [blackjack.md](blackjack.md)) |
| `GET /games/api/ledger?since=0&limit=500` | every game's; add `&game=roulette` (or `craps`, `russian`, `trivia`, `hexfall`, `climb`, `blackjack`) for one — an unknown name is a 404 `unknown game` |

```
curl "http://localhost:4747/games/api/roulette/ledger?since=0"
curl "http://localhost:4747/games/api/ledger?since=0&limit=1000"
```

```json
{"ok": true, "events": [
  {"seq": 51, "ts": 1790000000.123, "game": "roulette", "type": "debit", "user": "alice", "amount": 100,
   "reason": "bet", "bet_id": "rb-1a2b3c4d", "bet": "Red", "roll_id": null},
  {"seq": 54, "ts": 1790000031.402, "game": "roulette", "type": "credit", "user": "alice", "amount": 200,
   "reason": "win", "bet_id": "rb-1a2b3c4d", "bet": "Red", "roll_id": "r-5f6e7d8c"}
 ], "last_seq": 55, "truncated": false, "oldest_seq": 1}
```

(alice's 100 on red won: 100 stake + 100 won. Seqs 52, 53 and 55 are craps
events.)

- Each returns the events with `seq` **greater than** `since`, oldest first,
  at most `limit` of them (default 500, up to 5000).
- `seq` is unique across the games and only ever goes up — it keeps counting
  across restarts — so one game's events have gaps where the others' are.
  That's normal. `last_seq` is always the ledger's newest seq, of any game.
- `truncated` is `true` when you didn't get everything after `since`: either
  `limit` cut it short (call again from the last seq you processed), or some
  of those events are older than the 10 000 (of every game) kept in memory —
  `oldest_seq` is the oldest one still held; the rest are in
  `games_ledger.jsonl` (and `games_ledger.jsonl.1`).
- `type` is `debit` (coins the bank takes) or `credit` (coins the bank pays);
  `bet` is a readable label, `bet_id` the table bet, and `roll_id` the spin's
  `id` on a win (`null` otherwise). Craps', Russian Roulette's, Trivia's,
  Hexfall's, Soul Climb's and Blackjack's reasons are in their own docs; roulette's:

| `type` | `reason` | When |
| --- | --- | --- |
| debit | `bet` | a new bet went on the table |
| debit | `add` | more was added to a bet already down |
| credit | `win` | a bet won at the landing: stake + winnings |
| credit | `remove` | a bet was taken down |
| credit | `refund` | the table was cleared |

- Panel websockets (`/games/ws/panel`) get every new batch pushed, one
  message per game: `{"type": "ledger", "game": "roulette", "events": [...]}`.
- Of roulette's bets, only table bets are in it. Bets sent with a spin call
  never are.

### Board

`GET` or `POST /games/api/roulette/board` puts Hex's own lines up as the
"on the table" board — the display call for [Mode B](#two-ways-to-run-the-money),
where Hex keeps the bets. It's the same call as
[craps' board](craps.md#board--the-bets-that-are-down): `{"title", "bets": [{"user", "text", "amount"}]}`,
up to 100 lines, `user` up to 40 characters, `text` up to 60, `amount`
optional (a finite number from 0 to 1 000 000 000 000), each line with a
`user` or a `text`; `title` up to 40 characters (default `ON THE TABLE`);
`?user=&amount=&text=` as the one-line shorthand.

```
curl -X POST http://localhost:4747/games/api/roulette/board -H "Content-Type: application/json" -d "{\"title\":\"PLACE YOUR BETS\",\"bets\":[{\"user\":\"alice\",\"text\":\"Red, Split 17/20\",\"amount\":125},{\"user\":\"bob\",\"text\":\"Straight 17\",\"amount\":10}]}"
curl -X POST http://localhost:4747/games/api/roulette/board -H "Content-Type: application/json" -d "{\"bets\":[]}"
curl http://localhost:4747/games/api/roulette/board/clear
```

- It answers `{"ok": true, "table": TABLE}`, with the board in the table as
  `display_board`: the title, the cleaned lines (all three keys, `null` for
  what wasn't sent) and `total`, the sum of the amounts.
- While `display_board` isn't `null`, the overlay's board shows it
  **instead of** the table's bets, before the spin, in the same style:
  the title, up to **table lines** lines, the total. **show table** applies,
  and **show when bets** counts its lines. `{"bets": []}` is an empty board —
  Hex has nothing down — so no board is shown.
- `GET|POST /games/api/roulette/board/clear` (or `{"clear": true}`) sets it
  back to `null` — the board built from the table's bets again. It answers
  `{"ok": true, "cleared": true|false, "table": TABLE}`.
- Post it any time, even while the ball is in the air — the overlay holds
  table updates until the landing, so it never gives the spin away.
- Display only: it never touches the result, the table's bets, the ledger or
  the history. It lives in memory only — after a Hexcast restart
  `display_board` is `null` again, so post it again.
- **HTTP 400** when the body isn't valid JSON, when `bets` isn't a list of
  lines, when there's no `bets` (nor the one-line shorthand) and no `clear`,
  or when every line sent was unusable. Nothing changes then.

### Hex's side: the table (Mode A)

The table route with **Hex**, the channel's bot, as the bank: Hex's own code
keeps everyone's hexcoins in its **hexbank**, and Hexcast keeps none. It
works exactly like [craps' bank](craps.md#hooking-up-the-bank-hexcoins) —
the same `debits`, the same ledger, the same record on Hex's side — so a Hex
that already runs craps needs little more than a new command. In the steps
and the code, `hexbank.balance(user)`, `hexbank.debit(user, amount)` and
`hexbank.credit(user, amount)` are **placeholder names** — use whatever
Hex's hexbank really calls them.

Next to the hexbank Hex saves `last_seq` (the last ledger event it has
processed) and `done` (seqs it already handled straight from a reply), in
the same transaction as the hexbank change they go with — see
[What Hex saves](craps.md#what-hex-saves).

1. **Placing a bet.** Check `hexbank.balance(user)` covers the amount (sync
   the ledger first, so winnings that just landed count), then
   `POST /games/api/roulette/bet` with `{"user", "bet", "amount"}`. For each
   `debits[]` entry `{user, amount, bet_id, seq}`, call
   `hexbank.debit(user, amount)` and add `seq` to `done`, in one
   transaction. A **rejected** bet moved nothing — reply with its `error`. A
   **409** `bets_closed` placed nothing (the ball is in the air) — try again
   after `retry_in_ms`. If a debit **fails** (the viewer spent the coins
   somewhere else in Hex since the check), take the bet back down with
   `POST /remove {"bet_id"}`, and add the failed debit's `seq` and the
   refund's `seq` to `done`. Craps' [Step 1](craps.md#step-1--placing-a-bet)
   has the details, and the other safe ordering — debit first, credit back
   whatever wasn't accepted.
2. **Spinning.** `POST /games/api/roulette/timer` — say, for a mod's
   `!openbets` — or turn **auto-spin** on and let the first bet start the
   countdown. At zero the wheel spins with every bet on the table. A `!spin`
   that calls `/spin` works too.
3. **Paying out: tail the ledger.** `GET /games/api/ledger?since=<last_seq>&limit=1000`
   covers every game in one tail (or `/games/api/roulette/ledger` for
   roulette only). For each event, oldest first: a `seq` in `done` → skip it
   and drop it from `done`; a `credit` (`win`, `remove`, `refund`) →
   `hexbank.credit(user, amount)`; a `debit` Hex didn't take itself (a bet
   placed with the panel's **Place bet**, a curl, a `/bet` whose reply never
   reached Hex) → `hexbank.debit(user, amount)`. Then save that `seq` as
   `last_seq`, together with the hexbank change. Repeat until a call returns
   no events; then poll every second or two, or sync whenever a `ledger`
   message arrives on `ws://<host>:4747/games/ws/panel` (and once on every
   connect).
4. **Announcing.** A countdown's spin has no reply to wait for. Watch
   `GET /games/api/roulette/last` and announce each new spin `id` once:
   `spin.result` for the pocket, `spin.credits` for who won what (stake +
   winnings). A `/spin?wait=true` reply carries the same. Either way the
   coins move in the ledger tail, never from these.

**Where to start.** On a brand-new ledger, `last_seq` starts at 0. If Hex
already tails craps' ledger, keep its `last_seq` and point the tail at
`/games/api/ledger` — it's one seq space — but switch while nothing is on
the roulette table (press **Refund all & clear table** first if testing
left bets there): roulette events a craps-only tail has already passed are
never picked up again. Never skip events while bets are on a table — their
payouts would be for coins Hex never took.

**Restarts.** Hex restarting loses nothing: the tail picks up from the saved
`last_seq`. Hexcast restarting keeps every bet on the table (every one of
them already debited), and `seq` carries on where it stopped; a countdown
that was running is gone, so start the timer again (auto-spin counts again
from the next bet). A restart while the ball is in the air cancels that
spin — nothing settled, no credits written, the bets ride on the next spin —
so tell chat and start the timer again.

A sketch — any HTTP library works (`pip install requests` for this one):

```python
import threading, time, requests

API = "http://localhost:4747/games/api"   # Hexcast, as Hex's machine sees it
LOCK = threading.RLock()   # one money action at a time: bets, take-downs, the ledger tail

# Placeholders - the names are made up, use what Hex really has:
#   hexbank.balance(user) -> int
#   hexbank.debit(user, amount) -> bool     False when the viewer can't cover it
#   hexbank.credit(user, amount)
#   chat(text)                              a message in chat
#   state.last_seq, state.done (a set), state.last_spin   Hex's record, next to the hexbank
#   state.save()   commits that record TOGETHER with the hexbank changes made since the last save


def sync_ledger():
    """Apply every ledger event Hex hasn't handled yet (craps and roulette), oldest first, exactly once."""
    with LOCK:
        while True:
            r = requests.get(f"{API}/ledger", params={"since": state.last_seq, "limit": 1000}, timeout=10)
            fresh = [ev for ev in r.json().get("events") or [] if ev["seq"] > state.last_seq]
            if not fresh:
                return                                       # caught up
            for ev in fresh:                                 # oldest first
                if ev["seq"] in state.done:
                    state.done.discard(ev["seq"])            # handled from a /bet or /remove reply
                elif ev["type"] == "credit":
                    hexbank.credit(ev["user"], ev["amount"]) # win, remove, refund
                else:                                        # a bet Hex didn't take the coins for
                    hexbank.debit(ev["user"], ev["amount"])  # (panel, curl, a lost reply)
                state.last_seq = ev["seq"]
                state.save()


def on_bet(user, bet, amount):
    """!bet red 100 -> on the table for the next spin"""
    with LOCK:
        sync_ledger()                                        # winnings that just landed count
        if hexbank.balance(user) < amount:
            return f"@{user} you have {hexbank.balance(user)} hexcoins"
        r = requests.post(f"{API}/roulette/bet", json={"user": user, "bet": bet, "amount": amount}, timeout=10)
        data = r.json()
        if r.status_code == 409:
            return f"@{user} the ball is rolling - bet again in a moment"
        for d in data.get("debits") or []:
            if hexbank.debit(d["user"], d["amount"]):
                state.done.add(d["seq"])                     # the tail mustn't take it again
                state.save()
            else:                                            # spent elsewhere since the check
                take_back(d)
                return f"@{user} you don't have {amount} hexcoins any more"
    if not data.get("accepted"):
        return f"@{user} {data.get('error') or 'bet refused'}"
    b = data["accepted"][0]
    return f"@{user} {b['amount']} on {b['label']}, pays {b['odds']} to 1"


def take_back(d):
    """A debit failed: take that bet back down - nothing moves for coins Hex never took"""
    r = requests.post(f"{API}/roulette/remove", json={"bet_id": d["bet_id"]}, timeout=10)
    if r.status_code != 200:
        return                          # the ball is in the air: the tail takes the debit
    rm = r.json()
    state.done.add(d["seq"])                                 # never taken...
    state.done.update(ev["seq"] for ev in rm["ledger"])      # ...so its refund isn't paid either
    extra = rm["removed"][0]["refund"] - d["amount"]
    if extra > 0:
        hexbank.credit(d["user"], extra)                     # an add: the rest of the line was Hex's already
    state.save()


def on_open(seconds=30):
    """!openbets - the spin timer: at 0 the wheel spins with everything on the table"""
    r = requests.post(f"{API}/roulette/timer", json={"seconds": seconds}, timeout=10)
    data = r.json()
    if r.status_code == 409:
        return f"The wheel is busy - try again in {data['retry_in_ms'] // 1000 + 1}s"
    return f"Place your bets! The wheel spins in {data['auto_in_ms'] // 1000}s"


def watch():
    """Pays everything (countdown spins, panel spins, take-downs) and announces each landed spin once."""
    while True:
        try:
            sync_ledger()
            spin = requests.get(f"{API}/roulette/last", timeout=10).json()["spin"]
            if spin and spin["id"] != state.last_spin:
                state.last_spin = spin["id"]
                state.save()
                res = spin["result"]
                won = ", ".join(f"@{c['user']} +{c['amount']}" for c in spin["credits"]) or "no winners"
                chat(f"{res['number']} {res['color'].upper()}! {won}")
        except requests.RequestException:
            pass                                             # Hexcast restarting - try again
        time.sleep(2)
```

Run `watch()` on its own thread when Hex starts. The only places coins move
are `on_bet` (the debits Hex took for its own bets, and a give-back) and
`sync_ledger` (everything else); each `seq` is either marked `done` or
applied by the tail, never both.

### Hex's side: the spin timer only (Mode B)

Here Hex keeps the bets and does the math, as in
[Hex does the math](#hex-does-the-math-mode-b), and Hexcast adds the
countdown and the board. Hexcast's table stays empty — so nothing goes in
the ledger, and auto-spin never starts: Hex starts every countdown with
`/timer`.

1. **Open the round.** `POST /games/api/roulette/timer` with
   `{"seconds": 30}`. The reply's `auto_in_ms` says when the wheel spins;
   tell chat. A **409** busy means the last spin is still up: try again after
   `retry_in_ms`.
2. **Take bets in Hex** — Hex's rules, the stake from the hexbank — and
   mirror them after every change with `POST /games/api/roulette/board`, one
   line per viewer (`{"user": "alice", "text": "Red, Split 17/20", "amount": 125}`).
   The board sits beside the wheel under the countdown; send
   `"title": "PLACE YOUR BETS"` if you like, since a board Hex posts keeps
   its own title.
3. **Close bets** when the countdown runs out (at `auto_in_ms` after the
   reply — a second early is safer, since Hex's clock started a moment after
   Hexcast's), or as soon as any spin starts: `GET /games/api/status` shows
   roulette's `state` leaving `idle`. The pocket is picked the moment the
   ball is thrown, so no bet may come in after that.
4. **Wait for the landing.** Poll `GET /games/api/roulette/last` until its
   `spin.id` is new. Hexcast commits a spin when the ball lands — or at a
   **⏹ Stop** in mid-air; its result stands either way. That spin is the
   round's, even if someone spun early from the panel (any spin stops the
   countdown).
5. **Settle from `spin.result`** — at standard odds a bet wins when
   `result.number` is one of its `numbers` — and pay each winner from the
   hexbank.
6. **Show it.** `POST /games/api/roulette/announce` with `spin_id` set to
   the spin's `id` and one line per winner (or `"lines": []` and an
   `empty_text`), then post the board again: `{"bets": []}` until the next
   round's bets come in.

If the countdown is cancelled (the panel's **Cancel timer**,
`/timer/cancel`, **⏹ Stop**), nothing spins: `GET /games/api/status` shows
roulette `idle` with `table.auto_spin_in_ms` `null`, and `/last` hasn't
changed. The bets are still Hex's — start the timer again. A Hexcast
restart while the ball is in the air cancels the spin too (it never reaches
`/last`): settle nothing, and post the board again, since it's memory only.
Never also `/bet` Hex's bets — Hexcast would settle them and put the payouts
in the ledger as well.

The waiting part as a sketch, with `on_bet` taking bets into `pending` as in
the [Mode B sketch](#hex-does-the-math-mode-b) (and calling `post_board()`
after each one). `hexbank.credit` is a placeholder name again.

```python
import time, requests

API = "http://localhost:4747/games/api"
pending = []   # this round's bets - Hex's own list: {"user", "amount", "label", "numbers", "odds"}


def post_board():
    """Hex's bets on the overlay's board, one line per viewer (display only)"""
    per = {}
    for b in pending:
        per.setdefault(b["user"], []).append(b)
    requests.post(f"{API}/roulette/board", timeout=10, json={"title": "PLACE YOUR BETS", "bets": [
        {"user": u, "amount": sum(b["amount"] for b in bs), "text": ", ".join(b["label"] for b in bs)}
        for u, bs in per.items()]})


def roulette_state():
    return requests.get(f"{API}/status", timeout=10).json()["games"]["roulette"]


def run_round(seconds=30):
    """!openbets - one round on the spin timer (run it on its own thread): bets until 0, then settle"""
    global pending
    last = requests.get(f"{API}/roulette/last", timeout=10).json()["spin"]
    before = last["id"] if last else None
    r = requests.post(f"{API}/roulette/timer", json={"seconds": seconds}, timeout=10)
    if r.status_code == 409:
        return f"The wheel is busy - try again in {r.json()['retry_in_ms'] // 1000 + 1}s"
    close_at = time.monotonic() + r.json()["auto_in_ms"] / 1000 - 1     # a second early
    while time.monotonic() < close_at:
        time.sleep(0.5)
        try:
            if roulette_state()["state"] != "idle":
                break                                         # someone spun early
        except (requests.RequestException, ValueError):
            pass
    bets, pending = pending, []                               # bets from now on are for the next round
    while True:
        time.sleep(1)
        try:
            spin = requests.get(f"{API}/roulette/last", timeout=10).json()["spin"]
            if spin and spin["id"] != before:
                break                                         # landed (or stopped in mid-air): it stands
            st = roulette_state()
        except (requests.RequestException, ValueError):
            continue                                          # Hexcast restarting - ask again
        if st["state"] == "idle" and st["table"]["auto_spin_in_ms"] is None:
            pending = bets + pending                          # cancelled, or lost to a restart
            return "No spin this time - your bets are still on"
    res, lines = spin["result"], []                           # Hexcast's fair pocket; "number" is a string
    for b in bets:
        if res["number"] in b["numbers"]:
            hexbank.credit(b["user"], b["amount"] * (b["odds"] + 1))    # stake + winnings
            lines.append({"user": b["user"], "amount": b["amount"] * b["odds"], "text": b["label"]})
    try:
        requests.post(f"{API}/roulette/announce", timeout=10, json={
            "spin_id": spin["id"], "lines": lines, "empty_text": "House wins"})
        post_board()                                          # empty until the next round's bets
    except requests.RequestException:
        pass                                                  # display only - the payouts are done
    names = ", ".join(f"@{l['user']} +{l['amount']}" for l in lines) or "nobody"
    return f"{res['number']} {res['color'].upper()}! Winners: {names}"
```

---

## HTTP API

All endpoints return JSON with `"ok": true` or `"ok": false` (plus an
`"error"`). On this page `{game}` is `roulette` (Craps has the same generic
routes plus its own — see [docs/craps.md](craps.md#http-api)); an unknown name
gets a 404 `{"ok": false, "error": "unknown game"}`. No authentication (LAN tool,
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
| `GET\|POST /games/api/{game}/announce` | show a winners card from a bot that did its own math (see [Announce](#announce)) |
| `GET\|POST /games/api/{game}/announce/clear` | take that card down now |
| `GET /games/api/{game}/history?limit=20` | recent spins (newest first) + stats |
| `POST /games/api/{game}/history/clear` | clear history and stats (GET works too) |
| `GET /games/api/{game}/bets` | bet-type reference: types, syntax examples, odds |
| `GET /games/api/{game}/validate?bet=...` | parse one bet string without spinning |
| `GET\|POST /games/api/{game}/show` | put the idle wheel on screen |
| `GET\|POST /games/api/{game}/hide` | take it off screen |
| `GET\|POST /games/api/{game}/timer` · `timer/cancel` | start the countdown to the next spin now · stop it (see [Timer](#timer)) |
| `GET\|POST /games/api/roulette/bet` | put bets on the [table](#table--spin-timer) for the next spin (see [Bet](#bet)) |
| `GET\|POST /games/api/roulette/remove` | take table bets down: a refund (see [Remove and clear](#remove-and-clear)) |
| `GET /games/api/roulette/table` | the table: every bet, exposure, the countdown (see [Table](#table)) |
| `GET /games/api/roulette/user/{name}` | one viewer's table bets and session totals (see [User](#user)) |
| `GET\|POST /games/api/roulette/clear` | refund every table bet |
| `GET\|POST /games/api/roulette/board` · `board/clear` | Mode B: Hex's own board before the spin · back to the table's (see [Board](#board)) |
| `GET /games/api/roulette/ledger?since=0&limit=500` | roulette's ledger events after a seq (see [The shared ledger](#the-shared-ledger)) |
| `GET /games/api/ledger?since=0&limit=500&game=` | every game's ledger events, or one game's |
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
| `test` | `true` for a test spin: kept out of history and stats, no clip cues, the table's bets untouched |
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
  "settlements": [],
  "credits": [],
  "table_summary": {"bets": 0, "wagered": 0, "paid": 0, "net": 0},
  "overrides": {"theme": "neon", "result_position": "below"},
  "landed": true,
  "table": {"bets": [], "bets_open": true, "auto_spin_in_ms": null, "...": "..."},
  "ledger": []
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
- `settlements`, `credits`, `table_summary`, `table` and `ledger` — the
  bets on the [table](#table--spin-timer), settled on this spin (see
  [Settling a spin](#settling-a-spin)). Empty here: nothing was on the table,
  and bets sent with a spin never go in the ledger.
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
tell chat to wait. A [`/timer`](#timer) gets the same 409 — and table bets
don't: they're only refused (`bets_closed`) while the ball is in the air.

### Announce

`GET` or `POST /games/api/roulette/announce` puts a **winners card** on the
overlay for the current (or last) spin. It's the display call for
[Mode B](#two-ways-to-run-the-money), where Hex did the math: the card
**replaces** the winners list Hexcast would build, in the same style. It's
display only — it doesn't touch the result, any bets, the history or the
stats. Roulette and craps have it (on the craps tray it replaces the payouts board —
see [Craps → Announce and board](craps.md#announce-and-board)); the round games
(Russian Roulette, Trivia, Hexfall, Soul Climb, Blackjack) answer 400 — their game-over card is built in (`STATE.game.summary`). Parameters go
in the query string, a JSON body (POST), or both — the body wins where they
overlap.

```json
POST /games/api/roulette/announce
{
  "spin_id": "r-1a2b3c4d",
  "title": "WINNERS",
  "lines": [
    {"user": "alice", "amount": 850, "text": "Split 17/20"},
    {"user": "bob", "amount": -100, "text": "Corner 19"}
  ],
  "empty_text": "House wins",
  "seconds": 8,
  "currency": "hexcoins"
}
```

```
curl "http://localhost:4747/games/api/roulette/announce?user=alice&amount=850&text=Split%2017/20"
curl -X POST http://localhost:4747/games/api/roulette/announce -H "Content-Type: application/json" -d "{\"lines\":[{\"user\":\"alice\",\"amount\":850,\"text\":\"Split 17/20\"},{\"user\":\"bob\",\"amount\":-100,\"text\":\"Corner 19\"}],\"empty_text\":\"House wins\",\"seconds\":8,\"currency\":\"hexcoins\"}"
curl -X POST http://localhost:4747/games/api/roulette/announce -H "Content-Type: application/json" -d "{\"lines\":[],\"empty_text\":\"House wins\"}"
curl http://localhost:4747/games/api/roulette/announce/clear
```

(The curls leave out `spin_id` so they work as they are; a bot sends the id
of the spin it's announcing — see below.)

| Parameter | Default | What it does |
| --- | --- | --- |
| `lines` | — | the card's lines, `[{"user", "amount", "text"}]` (a single line object works too; in a query string, as JSON text) — up to 50 are kept, the rest dropped. Every field is optional, but a line needs a `user` or a `text`; a line that isn't usable (neither of those, or a bad `amount`) is skipped. `[]` means nobody won: the card shows `empty_text` |
| — `user` | — | who. Cleaned like a spin's `user`: control characters removed, a leading `@` dropped (the card adds its own), up to 40 characters |
| — `amount` | — | a finite number, at most 1 000 000 000 000 either way (`1e12`). Positive shows as `+200` (green), negative as `−50` (red), `0` as `0` (dim). Leave it out for no amount |
| — `text` | — | what for (`Split 17/20`), up to 60 characters, control characters removed |
| `user`, `amount`, `text` | — | query shorthand for one line: `?user=alice&amount=200&text=Red` (added after any `lines`) |
| `title` | `"WINNERS"` | the card's title, up to 40 characters |
| `empty_text` | `"No winners"` | shown instead of lines when there are none, up to 60 characters |
| `currency` | the game's `currency` setting (`"hexcoins"` unless you changed it) | shown after each amount, up to 24 characters; `""` for none |
| `seconds` | the rest of the result phase (all of it while the ball is in the air), else the game's **result seconds** | 1–120 — how long the card stays up. Posted while the ball is in the air, it counts from the landing |
| `spin_id` | the current spin (in the air or showing its result), else the last committed one | which spin the card is about — it has to be that same spin (see below) |

Response (HTTP 200):

```json
{
  "ok": true,
  "announce": {"id": "a-1a2b3c4d", "spin_id": "r-1a2b3c4d", "title": "WINNERS",
               "lines": [{"user": "alice", "amount": 850, "text": "Split 17/20"},
                         {"user": "bob", "amount": -100, "text": "Corner 19"}],
               "empty_text": "House wins", "currency": "hexcoins", "expires_in_ms": 8000},
  "state": {"state": "result", "visible": true, "announce": {"id": "a-1a2b3c4d", "...": "..."}, "...": "..."}
}
```

- **ANNOUNCE** — the `announce` object. It's also in every [STATE](#websocket-protocol)
  (websocket and `/games/api/status`) as `announce`, `null` when there's no
  card. `lines` are the cleaned lines, always with all three keys (`null`
  for what wasn't sent); `spin_id` is the spin it went with; `expires_in_ms`
  is how long the card has left, measured when the message was built (like
  `elapsed_ms`).
- **On screen** it replaces the winners list for that spin, in the same
  style: the title, up to **bets max** lines (then `+N more`), each with
  `@user`, the text and the amount (`+` green, `−` red, `0` dim) followed by
  the currency when there is one — or `empty_text` when there are no lines.
  **show bets** applies to it too: with it off, no card is shown.
- **Timing.** Post it whenever Hex is ready — straight after a `wait=true`
  spin comes back is the usual moment. Posted while the ball is still
  rolling, it's held until the ball lands, so it never spoils the spin; its
  `seconds` then count from the landing (and `expires_in_ms` includes the
  time left in the air).
- **How long.** The card stays up for `seconds`, and the wheel stays on
  screen that long too, even with **hide when idle** on. `/hide` and `/stop`
  still take the wheel off screen, card and all — but they don't clear the
  card, so a `/show` before its time is up brings it back. When the time is
  up the server takes the card down and every overlay and panel updates.
- **Clearing.** The next spin clears it; posting again replaces it.
  `GET|POST /games/api/roulette/announce/clear` clears it now and answers
  `{"ok": true, "cleared": true, "announce": null, "state": STATE}`
  (`cleared` is `false` if there was no card up). Cleared while the result
  is still up, the winners list Hexcast built comes back (if the spin
  settled any bets, sent with it or on the table).
- **`spin_id`** stops a late card from landing on the wrong spin. It must be
  the id of the current spin (in the air or showing its result) or, when
  there isn't one, of the last committed spin. Anything else gets **HTTP
  409** and nothing is shown:

  ```json
  {"ok": false, "error": "stale", "spin_id": "r-5e6f7a8b"}
  ```

  (`spin_id` there is the id it expected: the current spin's, or the last
  one's — `null` when there's none: no spin since startup or since the
  last history clear.) Without `spin_id` the card goes with the current
  spin, or else the last one.
- **HTTP 400** when the body isn't valid JSON, when `lines` isn't a list
  of lines, when neither `lines` (nor the one-line shorthand) nor
  `empty_text` was sent, or when every line sent was unusable and there's
  no `empty_text`.
  `"lines": []` on its own is fine — it shows `No winners`.

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
  spin's result phase ends. Useful for "place your bets" before a spin (the
  [spin timer](#timer) does that too, with a countdown).
- **hide** — take it off screen. It won't cut a spin off mid-air: while the
  ball is rolling it answers 409 busy.
- **stop** — the panic button (the panel's **⏹ Stop**): aborts every
  animation, stops any [countdown](#the-countdown-auto-spin-and-the-spin-timer)
  and hides everything immediately. A spin that had already started still
  counts — it's committed to history (unless it was a `test` spin), and the
  table's bets are settled and paid, since its result was already handed to
  whoever asked for it — and the game goes back to idle, ready for the next
  spin.

With **hide when idle** off, the wheel stays on screen between spins. With
**show when bets** on, it also stays up while bets are on the
[table](#table--spin-timer) (or Hex's [board](#board) has lines), and a
running countdown keeps it up too. A `/hide` (or a stop) still takes it off
until the next `/show`, `/timer` or spin; a countdown that's running after a
`/hide` keeps going, and its spin brings the wheel back.

### Config

```
curl http://localhost:4747/games/api/config
curl -X POST http://localhost:4747/games/api/config -H "Content-Type: application/json" -d "{\"roulette\":{\"spin_seconds\":12,\"cooldown_seconds\":30}}"
```

The config is stored as `{"roulette": {...}, "craps": {...}}` (the craps
keys are in [docs/craps.md](craps.md#config-keys)). `POST` merges whatever subset
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
| `show_bets` | `true` | the winners list after landing (only when the spin settled bets — sent with it or on the table), and a bot's [announce](#announce) card | ✓ |
| `bets_max` | `5` | 1–20 — winners listed (lines, on an announce card) | ✓ |
| `show_table` | `true` | the "on the table" board before the spin (the [table](#table--spin-timer)'s bets, or Hex's [board](#board)) | ✓ |
| `table_max` | `6` | 1–20 — board lines before `+N more` | ✓ |
| `table_position` | `"right"` | `right` \| `left` \| `below` \| `above` — where the board sits next to the wheel | ✓ |
| `show_rules` | `true` | the "how to play" box while bets are open: the bets, one line each, and what they pay (the theme's colours; the side the board doesn't take) | ✓ |
| `sfx` | `true` | built-in synthesized ball sounds in the overlay | ✓ |
| `sfx_volume` | `0.5` | 0–1 | ✓ |
| `spin_clip`, `land_clip` | `""` | soundboard clip names fired at launch / landing (`""` = none) | |
| `cooldown_seconds` | `0` | 0–3600 — extra lockout after the result phase ends (`0` = none) | |
| `currency` | `"hexcoins"` | 1–24 characters — the name on the board, the winners list and in the table; the [announce](#announce) card's default | |
| `min_bet` | `1` | whole number, 1–1 000 000 000 — the least a table bet may put down | |
| `max_bet` | `100000` | whole number, 0–1 000 000 000 000; `0` = no maximum — the most one table bet line may hold; a value below `min_bet` is reset to the default (or to `0` if `min_bet` is above that) | |
| `auto_spin` | `false` | spin by itself when the bet window runs out, while bets are on the table (see [the countdown](#the-countdown-auto-spin-and-the-spin-timer)) | |
| `bet_window_seconds` | `20` | 5–300 — the auto-spin countdown, and [`/timer`](#timer)'s default | |
| `show_when_bets` | `true` | keep the wheel on screen while bets are on the table, or Hex's `/board` has lines (even with hide when idle) | |

---

## WebSocket protocol

The overlay connects to `/games/ws/overlay`, the panel to `/games/ws/panel`;
both reconnect on their own. You only need this if you're building your own
overlay or dashboard — bots should use the HTTP API.

Server → client (JSON text frames):

- `{"type": "config", "config": {"roulette": {...}, "craps": {...}}}` — on
  connect and after every config save.
- `{"type": "state", "game": "roulette", "state": STATE}` — on connect (one
  per game) and on
  every transition: spin start, landing, result end, cooldown end, show,
  hide, stop, history clear, when an [announce](#announce) card is
  posted, cleared or runs out, and whenever the [table](#table) changes (a
  bet, a take-down, a clear, Hex's board, a countdown started or stopped).
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
  "busy_ms": 12860,
  "announce": null,
  "table": {"bets": [...], "bets_open": false, "auto_spin_in_ms": null, ...}
}
```

- `state` — `idle`, `spinning`, `result` or `cooldown`.
- `visible` — whether the wheel should be on screen: during a spin and its
  result, after `/show`, while an [announce](#announce) card is up, while a
  [countdown](#the-countdown-auto-spin-and-the-spin-timer) runs, while bets
  are on the table or Hex's board has lines (with **show when bets** on), or
  always when `hide_when_idle` is off. After `/hide` or a stop it's `false`
  until the next `/show`, `/timer` or spin.
- `spin` — the spin in the air or in its result phase (`null` when idle),
  with `elapsed_ms`: milliseconds since it started, measured on the server
  when the message was built. Clients seek by `elapsed_ms` and never compare
  epoch times, so clock skew between machines doesn't matter.
- `last` — the last committed spin.
- `history` — up to 20 results, newest first. It only ever contains
  **landed** spins, so it never spoils the one in the air.
- `busy_ms` — how long until the game accepts a new spin.
- `announce` — the winners card a bot posted with [`/announce`](#announce)
  (the ANNOUNCE object, with `expires_in_ms` measured when the message was
  built), or `null` when there isn't one.
- `table` — the roulette [TABLE](#table): the bets waiting for the next
  spin, the countdown's `auto_spin_in_ms` (measured when the message was
  built) and Hex's `display_board`. The overlay holds a table that arrives
  while the ball is in the air until it lands, so it never gives the spin
  away.

**Panel** sockets (`/games/ws/panel`) also get
`{"type": "ledger", "game": "roulette", "events": [...]}` for every new batch
of [ledger](#the-shared-ledger) events — one message per game, so a game's
events never mix with the other's. Overlays never get it (and ignore message
types they don't know). Craps uses the same messages, with its own table in
its `STATE` — see [Craps → WebSocket](craps.md#websocket).

Client → server: `{"type": "hello", "game": "roulette"}` (optional) and
`{"type": "error", "message": "..."}` — overlays report script errors this
way and they land in `hexcast.log`. Unknown types are ignored.

`GET /games/api/status` returns
`{"ok": true, "connected": true, "overlays": 1, "games": {"roulette": STATE, "craps": STATE}}`,
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
command flow, with the bets kept in the bot until the spin call (to let
Hexcast hold them on its table instead, with a countdown and the ledger, see
[Table & spin timer](#table--spin-timer)):

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

For Hex's hexbank — checking amounts, paying each bet exactly once, and what
to do when Hex or Hexcast restarts — see
[Hooking up the bank](#hooking-up-the-bank-hexcoins).

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

### Hex does the math (Mode B)

Here Hex keeps the bets and works out the payouts in its own code, and
Hexcast spins the wheel and shows the winners card Hex posts (see
[Two ways to run the money](#two-ways-to-run-the-money)). The pocket is still
Hexcast's fair spin; Hex only reads it. This recipe spins on Hex's command;
to count down to the spin with Hex's bets on the board meanwhile, see
[Hex's side: the spin timer only](#hexs-side-the-spin-timer-only-mode-b).

1. **Take bets in Hex.** For each `!bet <bet> <amount>`, Hex checks the bet
   against its own rules, takes the stake from its hexbank, and adds the bet
   to its own list for this round. To understand roulette bet text, Hex can
   still call `GET /games/api/roulette/validate?bet=<bet>`: it returns the
   `numbers` the bet covers and its `odds`, and changes nothing. `/show` puts
   the wheel on screen while bets are open, and [`/board`](#board) can show
   Hex's bets beside it.
2. **Spin without bets.** Take this round's bets out of the list (bets that
   arrive from now on are for the next spin), then
   `POST /games/api/roulette/spin` with `{"user": ..., "wait": true}` and
   **no `bets`**. On a **409**, put the round back and try again after
   `retry_in_ms`.
3. **Work out the payouts from `result`.** At standard odds a bet wins when
   `result.number` is one of its `numbers` (both are strings, so `"00"` never
   matches `"0"`), and pays `amount × (odds + 1)` back — but in Mode B the
   rules are whatever Hex says.
4. **Pay** each winner from the hexbank.
5. **Announce.** `POST /games/api/roulette/announce` with `spin_id` set to
   the spin's `id` and one line per winner. The card replaces the winners
   list on the overlay. With no winners, send `"lines": []` and an
   `empty_text` (`House wins`).

If the spin call gets no reply at all (a timeout, or Hexcast restarting),
settle nothing — the bets are still Hex's — and spin again. A reply with
`"landed": false, "stopped": true` (someone pressed **⏹ Stop**) still has a
result that stands: settle it.

The same flow as a Python sketch. `hexbank.debit(user, amount)` (returns
`False` when the viewer can't cover it) and `hexbank.credit(user, amount)`
are placeholder names — use whatever Hex's hexbank really calls them.

```python
import requests

HEX = "http://localhost:4747/games/api/roulette"
pending = []   # this round's bets - Hex's own list

def on_bet(user, bet, amount):
    """!bet red 100 - Hex's rules and Hex's hexbank; /validate only reads the bet text"""
    v = requests.get(f"{HEX}/validate", params={"bet": bet}, timeout=10).json()
    if not v.get("valid"):
        return f"@{user} {v.get('error', 'invalid bet')}"
    if not hexbank.debit(user, amount):
        return f"@{user} you don't have {amount} hexcoins"
    pending.append({"user": user, "amount": amount, "label": v["label"],
                    "numbers": v["numbers"], "odds": v["odds"]})
    return f"@{user} {amount} on {v['label']}, pays {v['odds']} to 1"

def on_spin(mod):
    """!spin - Hexcast picks the pocket; Hex pays its own bets and posts the winners card"""
    global pending
    bets, pending = pending, []           # bets placed from now on wait for the next spin
    try:
        r = requests.post(f"{HEX}/spin", json={"user": mod, "wait": True}, timeout=60)  # no "bets"
        data = r.json()
    except (requests.RequestException, ValueError):
        pending = bets + pending          # no reply: nothing to settle, spin again
        return "The spin didn't go through - your bets are still on, spin again"
    if not data.get("ok"):
        pending = bets + pending          # keep the round for the next try
        if r.status_code == 409:
            return f"The wheel is busy, try again in {data['retry_in_ms'] // 1000 + 1}s"
        return f"Roulette error: {data.get('error', r.status_code)}"
    res = data["result"]                  # Hexcast's fair pocket; "number" is a string
    lines = []
    for b in bets:
        if res["number"] in b["numbers"]:
            hexbank.credit(b["user"], b["amount"] * (b["odds"] + 1))    # stake + winnings
            lines.append({"user": b["user"], "amount": b["amount"] * b["odds"], "text": b["label"]})
    try:
        requests.post(f"{HEX}/announce", timeout=10, json={
            "spin_id": data["id"], "lines": lines, "empty_text": "House wins", "currency": "hexcoins"})
    except requests.RequestException:
        pass                              # display only - the payouts are already done
    names = ", ".join(f"@{l['user']} +{l['amount']}" for l in lines) or "nobody"
    return f"{res['number']} {res['color'].upper()}! Winners: {names}"
```

Hex's list of bets is Hex's to keep safe across its own restarts — save it
together with the hexbank changes, like the rest of Hex's state.

---

## Hooking up the bank (hexcoins)

This is **Mode A** — Hexcast does the bet math (for Mode B, see
[Hex does the math](#hex-does-the-math-mode-b)) — with the bets sent on the
spin call. The other Mode A route, the [table and the spin timer](#table--spin-timer),
is the durable one: its bets and payouts are in the ledger, and Hex's side
is [its own short guide](#hexs-side-the-table-mode-a), the same as craps'.
Pick one route per bet.

The bank is **Hex**, the channel's bot. Hex's own code keeps everyone's
hexcoins in its **hexbank**; Hexcast keeps none. Hex takes the chat commands,
calls the Roulette API, and moves the coins in its hexbank. This section is
for whoever writes Hex's side.

`hexbank.balance(user)`, `hexbank.debit(user, amount)` and
`hexbank.credit(user, amount)` below are **placeholder names** — use whatever
Hex's hexbank really calls them.

The short version:

- **Taking a bet** — Hex checks the bet text with `/validate` and the amount
  itself, then debits the stake. A bet `/validate` rejects is never debited.
- **Spinning** — one `/spin` with every bet of the round in `bets`, and
  `wait: true`.
- **Paying out** — Hex credits every bet's `returned` from the reply: stake +
  winnings for a win, `0` for a loss, the stake back for a bet Hexcast
  couldn't read.
- **No ledger on this route.** Bets sent with a spin never go in the
  ledger, so Hexcast keeps no record of what was paid. Hex pays from the
  reply straight away and keeps its own note of which bets it has paid. (The
  [table route](#table--spin-timer) has the ledger, like
  [craps](craps.md#hooking-up-the-bank-hexcoins).)

### Who does what

- **Hexcast** picks the pocket (the fair spin), resolves every bet sent with
  the spin at standard odds, and animates it. It never sees a balance and
  never refuses a bet for lack of coins.
- **Hex** runs the betting window, holds the balances, takes each stake, sends
  the round's bets with the spin, and credits what comes back.

Each resolved bet carries the `user` string Hex sent, cleaned: a leading `@`
dropped, up to 40 characters, case unchanged. The bet text is trimmed and
kept up to 100 characters. Send names and bets in that form already (the
viewer's lowercase login is a good choice), so what comes back matches Hex's
own record exactly — the restart check below relies on it.

### Step 1 — taking bets

Hex collects bets in its own betting window (`!openbets` … `!spin`, like
[A chat betting round](#a-chat-betting-round); `/show` puts the wheel on
screen meanwhile). For each `!bet <bet> <amount>`:

1. **Check the amount yourself**: a positive whole number of hexcoins, at
   most `1e12`. `/validate` reads the bet **text** only — it never looks at an
   amount.
2. `GET /games/api/roulette/validate?bet=<bet>`. If `valid` is `false`, reply
   with its `error` and take nothing.
3. Check `hexbank.balance(user)` covers the stake, then
   `hexbank.debit(user, amount)` and add `{"user", "bet", "amount"}` to the
   round, in one transaction.
4. Confirm in chat with `label` and `odds`
   (`@bob 100 on Split 17/20, pays 17 to 1`).

Why the amount is Hex's job: in a spin, an `amount` that's negative, not a
number, or over `1e12` makes the bet **invalid with `returned: 0`** — Hexcast
couldn't read a stake, so there's nothing to refund (the bet comes back with
`amount: 0`). Only a bad bet **string** is refunded in full (`returned` =
`amount`). Roulette doesn't insist on whole coins either — `12.5` would be
accepted and paid `12.5 × (odds + 1)` — so send exactly the whole number Hex
took. The odds are whole numbers, so whole stakes always pay whole coins: no
rounding anywhere. A spin takes at most **200 bets**; any more are dropped
without a word (they aren't in the reply), so close the window at 200 or split
the round over two spins.

### Step 2 — spinning

`POST /games/api/roulette/spin` with every bet of the round:

```json
{"user": "hexmod", "wait": true,
 "bets": [{"user": "bob", "bet": "red", "amount": 100},
          {"user": "amy", "bet": "split:17/20", "amount": 10}]}
```

- With `"wait": true` the reply comes when the ball lands, with `result` and
  every bet resolved (`valid`, `win`, `payout`, `returned`), in the order
  they were sent. Without `wait` the same reply comes at once — hold the chat
  message (and the credits, so a balance check doesn't spoil the spin) until
  `lands_at` (unix seconds).
- **409 busy** — `{"error": "busy", "retry_in_ms", "state"}` while another
  spin is spinning, showing its result or in cooldown. Nothing spun and
  nothing was settled; Hex still holds the stakes. Keep the round and try
  again after `retry_in_ms`. Any other error reply (a 400) spun nothing
  either.
- **⏹ Stop** — a `/stop` (or the panel's Stop) while the ball is in the air
  still **commits the spin**. The `wait=true` reply arrives with
  `"landed": false, "stopped": true` and the full result and bets: credit as
  normal.
- **`test: true` spins never count.** They're animation only: kept out of
  history and stats, no clip cues. Never send real bets with one.

### Step 3 — paying out

For each bet in the reply's `bets`, credit `returned` to the viewer Hex took
the stake from (the bets come back in the order sent):

| The bet | `returned` | Hex |
| --- | --- | --- |
| won (`win: true`) | `amount × (odds + 1)` — stake + winnings | `hexbank.credit(user, returned)` |
| lost | `0` | nothing — the stake stays with the house |
| invalid bet text (`valid: false`) | `amount` — the stake back | `hexbank.credit(user, returned)`; never happens if every bet went through `/validate` first |
| invalid amount | `0` | nothing to pay — and nothing was taken if Hex checked the amount (step 1) |

Mark each bet paid — the spin `id` and the bet's index in `bets` — **in the
same transaction as its credit**. Then use `result` (`number`, `color`) and
`summary` for the chat message; the overlay shows the same winners list.

### Restarts and paying exactly once

Bets on the spin call have **no ledger**. Hexcast remembers committed spins
only in memory: the last 200, newest first, emptied when Hexcast restarts
(and by **Clear history** / `/history/clear`). So Hex keeps its own record of
each round:

1. Before calling `/spin`, save the round — its bets (stakes already taken)
   and that it's been **sent**.
2. When the reply arrives, save the spin `id` and the reply's resolved `bets`
   with the round, then credit bet by bet, marking each `(id, index)` paid
   with its credit.

On restart Hex finishes what it had started:

- **A round with a saved reply** — pay the indexes not yet marked paid, from
  the saved reply.
- **A round sent but with no reply** (Hex crashed mid-call, or the call timed
  out) — look for its spin in
  `GET /games/api/roulette/history?limit=200`: each SPIN there carries its
  resolved `bets` with `returned` (`GET /games/api/roulette/last` has just
  the latest). It's the spin whose `bets` list the same users, bets and
  amounts in the same order, with an `id` Hex hasn't paid yet. Found: pay it
  as above. Not found, and roulette isn't spinning (`/games/api/status`):
  it never happened — the stakes are still Hex's, so spin again or refund.
- **Hexcast restarts while the ball is in the air** — that spin is **never
  committed**. Hex's `wait=true` call fails with a connection error and the
  spin won't be in history afterwards. Pay nothing for it; spin again (or
  refund) once Hexcast is back.

History is a same-session safety net, not a durable record: if Hexcast
restarts after a spin landed but before Hex saved the reply, that spin is gone
from history and Hex can't tell it happened. Pay from the `wait=true` reply
promptly. For a durable record, put the bets on the
[table](#table--spin-timer) instead: there the [ledger](#the-shared-ledger)
replaces this history check — it's saved to disk, and tailing it pays every
win exactly once, whichever side restarts, with no rounds to match up.

### Hex's side in code (Python sketch)

`store.transaction()` stands for Hex's own storage — one database commit or
one file write around the hexbank change and the round it goes with. A round
starts as `{"bets": [], "paid": [], "state": "open"}`.

```python
import requests

HEX = "http://localhost:4747/games/api"
MAX_STAKE = 10 ** 12

def on_bet(rnd, user, bet, amount):
    """!bet red 100 while the window is open - the amount is Hex's job, the bet text /validate's"""
    if not isinstance(amount, int) or not 0 < amount <= MAX_STAKE:
        return f"@{user} bet a whole number of hexcoins"
    if len(rnd["bets"]) >= 200:
        return f"@{user} the table is full - next spin"
    v = requests.get(f"{HEX}/roulette/validate", params={"bet": bet}, timeout=10).json()
    if not v.get("valid"):
        return f"@{user} {v.get('error', 'invalid bet')}"          # rejected: nothing taken
    if hexbank.balance(user) < amount:
        return f"@{user} you don't have {amount} hexcoins"
    with store.transaction():
        hexbank.debit(user, amount)
        rnd["bets"].append({"user": user, "bet": bet, "amount": amount})
    return f"@{user} {amount} on {v['label']}, pays {v['odds']} to 1"

def on_spin(rnd, mod):
    """!spin - one spin for the whole round, answered when the ball lands"""
    with store.transaction():
        rnd["state"] = "sent"                                      # stakes taken, no reply yet
    try:
        r = requests.post(f"{HEX}/roulette/spin", timeout=90,
                          json={"user": mod, "wait": True, "bets": rnd["bets"]})
        spin = r.json()
    except (requests.RequestException, ValueError):
        return recover(rnd)                                        # no reply: find out first
    if not spin.get("ok"):                                         # 409 busy (or a 400): nothing spun
        with store.transaction():
            rnd["state"] = "open"                                  # Hex still holds the stakes
        if r.status_code == 409:
            return f"The wheel is busy, try again in {spin['retry_in_ms'] // 1000 + 1}s"
        return f"Roulette error: {spin.get('error', r.status_code)}"
    return pay(rnd, spin)                                          # "stopped": true pays the same

def pay(rnd, spin=None):
    """Credit every bet's `returned` exactly once (spin=None: resume from the saved reply)"""
    if spin is not None:
        with store.transaction():
            rnd.update(state="paying", spin_id=spin["id"], result=spin["result"], resolved=spin["bets"])
    for i, b in enumerate(rnd["resolved"]):
        if i in rnd["paid"]:
            continue
        with store.transaction():                                  # the credit and its mark together
            if b["returned"] > 0:
                hexbank.credit(rnd["bets"][i]["user"], b["returned"])
            rnd["paid"].append(i)
    with store.transaction():
        rnd["state"] = "done"
    res = rnd["result"]
    wins = [f"@{rnd['bets'][i]['user']} +{b['returned']}" for i, b in enumerate(rnd["resolved"]) if b["win"]]
    return f"{res['number']} {res['color'].upper()}! Winners: {', '.join(wins) or 'nobody'}"

def recover(rnd):
    """On Hex's start for every round not "done" or "open", and after a /spin with no reply"""
    if rnd["state"] == "paying":
        return pay(rnd)
    spins = requests.get(f"{HEX}/roulette/history", params={"limit": 200}, timeout=10).json()["history"]
    sent = [(b["user"], b["bet"], b["amount"]) for b in rnd["bets"]]
    for spin in spins:                                             # newest first; this Hexcast session only
        if [(b["user"], b["bet"], b["amount"]) for b in spin["bets"]] == sent and not store.spin_paid(spin["id"]):
            return pay(rnd, spin)
    if requests.get(f"{HEX}/status", timeout=10).json()["games"]["roulette"]["state"] == "spinning":
        return "Still spinning - recover again once the ball lands"
    with store.transaction():
        rnd["state"] = "open"                                      # it never spun: the stakes are still held
    return "That spin didn't happen - your bets are still on, spin again"
```

**The alternative — Mode B.** If Hex would rather keep its own odds and rules,
it spins **without** `bets`, settles each bet from the RESULT (`number`,
`color`, `parity`, `range`, `dozen`, `column`), pays from the hexbank and
posts the winners with `/announce` — see
[Hex does the math (Mode B)](#hex-does-the-math-mode-b).

---

## Storage

| File (in `config/`, or `HEXCAST_CONFIG_DIR`) | What |
| --- | --- |
| `games.json` | settings (the `"roulette"` and `"craps"` sections) |
| `games_roulette_table.json` | roulette's [table](#table--spin-timer): every bet waiting for the next spin — saved after every change |
| `games_craps_table.json` | craps' table (see [Craps → Storage](craps.md#storage)) |
| `games_ledger.jsonl` | the [shared ledger](#the-shared-ledger), every game's events, one JSON event per line (`.1` is the previous 10 MB) |

Spin history and stats are in-memory only — up to 200 spins, reset on
restart — and so are Hex's [board](#board) and [announce](#announce) card.
Don't delete `games_roulette_table.json` while bets are on it — those coins
were debited; use **Refund all & clear table** so the refunds go through the
ledger. No tokens, no secrets — same security posture as the rest of
Hexcast: no auth, keep it on the LAN.

---

## Troubleshooting

**`/games` or the API gives a 404.** The module loads at startup — restart
Hexcast after installing the plugin, and check the console output for errors.

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
(`retry_in_ms`). **⏹ Stop** (or `/games/api/stop`) ends it at once. A
`/timer` gets the same answer: start the next countdown after `retry_in_ms`.

**A table bet, take-down or clear returns 409 `bets_closed`.** The ball is in
the air. The table reopens the moment it lands — retry after `retry_in_ms`.

**The countdown doesn't start on its own.** Auto-spin needs **Auto-spin
after the bet window** on, bets on Hexcast's table (a board Hex posted
doesn't count) and an idle wheel; it starts with the next bet placed, or
when the last spin's result and cooldown are over. Or start one with **Start
timer** / `/timer`, which needs neither bets nor auto-spin.

**The countdown vanished before zero.** Any spin stops it (a test spin
from the editor too), and so do **⏹ Stop**, **Cancel timer** and **Refund
all**; an auto-spin countdown also stops when the last bet is taken down or
auto-spin is switched off.

**The wheel stays on screen between spins.** That's **show when bets** —
it stays up while bets are on the table or Hex's [board](#board) has lines
(Mode B: post `{"bets": []}` when nothing is riding) — or a countdown
running. Turn **Keep it on screen while bets are down** off in Settings, or
`/hide` it.

**`hexcast.log` says "games_roulette_table.json is older than the ledger
(table at seq X, ledger at Y)".** A save of the table failed (disk full, or
the file locked by antivirus / a sync tool) and Hexcast stopped before the
next save. The bets moved by the roulette seqs the line names may be missing
from — or still on — the table. Before the next spin, compare the **On the
table** card with those ledger events, and take down or re-place bets by
hand to match.

**A bet comes back `valid: false`.** Read its `error`. The usual suspects:
split numbers have to touch on the table (`18/19` don't); a corner has to be
a 2×2 block (its lowest number can't be in column 3); the only zero bets are
the ones listed in the [bet reference](#bet-reference) — so `0/3`, `0/2/3` and
first four (`0/1/2/3`) aren't bets here; use the basket, `0/00/1/2/3`.

**`/announce` returns 409 "stale".** The `spin_id` you sent isn't the current
spin (or, with none in progress, the last committed one) — another spin has
started since. The reply's `spin_id` is the one it expected. Skip the card for
the old spin (tell chat instead); don't re-post it without `spin_id`, or it
would land on the new spin.

**The announce card doesn't show, or goes too soon.** Posted while the ball is
rolling, it waits for the landing. By default it stays up for the rest of the
result phase (or **result seconds** once that's over) — send `seconds` (up to
120) for longer. A line with neither a `user` nor a `text`, or with an
`amount` that isn't a number, is skipped, and with **show bets** off no card
is shown at all. The next spin and `/announce/clear` take the card down;
`/hide` and **⏹ Stop** hide the wheel with it, but the card runs on until its
`seconds` are up. Check `announce` in `GET /games/api/status`: `null` means
there's no card.

**History is empty after a restart.** By design — history and stats live in
memory. Settings, the table's bets and the ledger persist.

**Test spins aren't in the history.** Also by design: `test` spins are shown
on stream but never counted, never fire clip cues and never settle the
table's bets (those ride on the next real spin).
