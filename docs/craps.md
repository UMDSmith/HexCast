# Craps — bank craps on stream, played with hexcoins

The second game in the [Games](games.md) module. A craps tray sits on the
stream with a table of bets that chat has put down — pass line, odds, come,
place, hard ways, the field, the props. Bets stay on the table from roll to
roll, exactly like a casino table. When someone rolls, the **server** throws
two fair dice, the overlay animates the throw landing on exactly those faces,
the stickman calls it (`YO-LEVEN`, `POINT IS 6`, `SEVEN OUT`), the puck moves,
and every bet with action settles by standard casino rules.

Bets are made with the channel currency, **hexcoins**. Hexcast doesn't hold
anyone's balance. The channel's bot, **Hex**, is **the bank**: its own
**hexbank** holds everyone's coins. Hexcast tells Hex exactly what to take
and what to pay, and keeps a numbered, persisted **ledger** of every coin
movement (one ledger for every game — roulette's table writes to it too), so
Hex can stay exactly in step, even across restarts. Or, if
Hex would rather do the math itself, it keeps the bets in its own code,
settles them from each roll, and just tells the overlay what to show.

Everything lives under `/games/*`, next to Roulette.

> **Wiring up Hex?** First pick [who does the math](#two-ways-to-run-the-money).
> Hexcast (Mode A): read [Hooking up the bank](#hooking-up-the-bank-hexcoins),
> the step-by-step guide for Hex's developer, with a chat-command flow and
> sample code. Hex (Mode B): read [Hex does the math](#hex-does-the-math-mode-b).
> Either way the dice are Hexcast's fair roll.

---

## Fairness

- Each die is rolled on the server with Python's `secrets.SystemRandom()`
  (the operating system's cryptographic random source): `randint(1, 6)` twice.
  Every face has exactly the same chance on every roll, whatever came before.
- The overlay and the panel only **animate** the server's dice. There is no
  parameter, config key or panel button that picks or nudges the dice — not
  bets, not the shooter, not test rolls, not per-roll overrides.
- Each roll carries a `seed`, but it only varies the *animation* (how the dice
  tumble and bounce), never the outcome.

Two dice make 36 equally likely combinations, so the totals come up this
often:

| Total | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ways | 1 | 2 | 3 | 4 | 5 | 6 | 5 | 4 | 3 | 2 | 1 |
| Chance | 2.8% | 5.6% | 8.3% | 11.1% | 13.9% | 16.7% | 13.9% | 11.1% | 8.3% | 5.6% | 2.8% |

---

## Install

Craps is an add-on of the [Games](games.md) plugin. Install **Games** from the **+** tab, then click the **+** in the
Games tab and install **Craps** (or `python hexcast.py plugins install games games_craps`). There are no extra Python
packages. The ledger is shared with the other table games; installing or removing a game never touches the others' bets.

---

## Browser source

| Source | URL |
| --- | --- |
| All games | `http://localhost:4747/games/overlay` |
| Craps only | `http://localhost:4747/games/overlay?game=craps` |

Add it as a **1920×1080** Browser source (match your canvas) — the page is
transparent and the tray sits on a 1920×1080 stage that scales to whatever
size you give the source. One source can show every game; or give craps its
own source with `?game=craps` so it sits on its own layer in OBS.

- Uncheck **Shutdown source when not visible** (otherwise the websocket dies)
  and **Refresh browser when scene becomes active**.
- Turn **Control audio via OBS** on. The built-in dice sounds — the rattle on
  the throw, the clacks off the felt and the back wall, the settle click — are
  synthesized inside the overlay, so this is how they reach your mixer.
- **Roll/landing clips** (see [Soundboard clip cues](#soundboard-clip-cues))
  play on the soundboard overlay (`/overlay`), not this one — keep both
  browser sources in the scene.

The **OBS browser source** card on the Craps tab shows both URLs for the
machine you're on, each with a **Copy** button.

---

## What viewers see

The tray is a casino craps tray: a padded rail, textured felt, the back wall
lined with the pyramid-diamond rubber, and a **point row** across the top —
`4 5 SIX 8 NINE 10` — with the round **puck**. During the come-out the puck
sits black-side up (**OFF**) at the left end; once a point is set it flips
white (**ON**) and sits on the point box.

1. The tray **pops in** at its saved placement when a roll starts (or stays
   up — see [Show, hide, stop](#history-show-hide-stop)).
2. The dice are **thrown in** from the left edge, tumbling, fly in an arc,
   bounce on the felt, hit the diamond back wall on the right, rebound, roll
   and settle — showing the server's faces up exactly when the roll time runs
   out. The faces only become final at landing, so nothing is spoiled early.
3. The **stickman call** pops: the big call (`SEVEN OUT`, `WINNER 6`,
   `YO-LEVEN`, …) with a short line under it (`Line away`, `Pay the line`, …).
4. The **puck** moves — ON to the new point, or back to OFF.
5. If any bets had action and **show payouts** is on, a **payouts board**
   lists the winners (`@alice +175 hexcoins`, up to **payouts max**, then
   `+N more`). With no winners it says `NO WINNERS`, and how much the house
   collects. When Hex does the math (Mode B), the card it posts with
   [`/announce`](#announce-and-board) shows here instead, in the same style.
6. The **history strip** (last **history count** rolls, 7s highlighted)
   updates only *after* the dice land.

Between rolls, with **show bets** on, a **bets board** shows who has what
down, one line per viewer (`@alice  Pass 100 +50 odds · Place 6 60`, with
bets that are off marked `(off)`), up to **bets max** viewers then `+N more`,
and the total on the table. In Mode B it shows the board Hex posts with
[`/board`](#announce-and-board) instead. With **show user** on, a caption shows the
shooter (`@bob is shooting · roll 4`, or `@bob rolls the dice` while they're
in the air). With a countdown running — [auto-roll](#auto-roll) or the
[roll timer](#roll-timer) — a **NEXT ROLL 0:12** timer ticks, and the tray
stays on screen while it does.

Every overlay and the panel's live preview render the **identical** throw:
the animation is seeded, and the server tells each one how far into the roll
it is. A source that (re)connects mid-roll jumps straight to the right moment.

**Themes:** `classic` (casino-green felt), `neon` (black with Hexcast-red
neon), `midnight` (navy), `royal` (purple and gold).
**Dice styles:** `red` (translucent casino red, white pips), `white` (ivory,
black pips), `black` (white pips), `gold`.

**Size:** at scale 1 the tray is 720×405 px on the 1920×1080 canvas, so scale
2 is 1440×810. It's placed by its centre. The boards, call banner and dice
shadows can reach a little outside the tray, so leave some room around it.

---

## The panel

Open `/games` and pick the **Craps** tab (`/games#craps` opens straight to
it). The top bar's **↗ Overlay**, **⏹ Stop** and **Edit Mode** are shared
with Roulette.

1. **OBS browser source** — the all-games URL and the `?game=craps` one for
   this host, each with **Copy**.
2. **Craps** — a live mirror: a 16:9 preview holding a scaled 1920×1080
   stage with the real tray (sound off), playing the same rolls as the
   overlay. **Roll dice** throws them (with an optional shooter name),
   **Show** / **Hide** put the idle table on or off screen, and **Start
   timer** / **Cancel timer** run the [roll timer](#roll-timer) (a countdown
   of the bet window). Readouts show the state (idle, rolling, result,
   cooldown) with its countdown, the phase and point, shooter, hand rolls,
   what's on the table, the countdown (auto-roll's or the timer's), whether
   bets are open, and the last roll.
3. **On the table** — every bet riding right now: user, bet, amount, odds,
   working/off. Bets that can come down have a **Take down** button (with a
   confirm). **Odds down** takes down only the odds: on a contract bet (pass
   after the point, a travelled come bet) it's the only button, and a don't
   pass / don't come with lay odds has it next to **Take down**. A viewer with
   two or more things that can come down also gets **Take down all**. Totals
   per user, and **Refund all & clear table** (with a confirm), which refunds
   every bet and resets to a come-out.
4. **Place a bet** — for testing and admin. **User**, **Amount**, **Bet** and
   **Odds on** (which line or come bet the odds go behind — or *auto*, from
   the bet text), **Check** (a dry run, nothing moves) and **Place bet**. With
   a bet picked under **Odds on**, **User** and **Bet** can stay empty: the
   odds go behind that bet, for its owner. **Placing is real**: the bet goes
   on the table and a debit goes in the ledger, so Hex's ledger tail takes
   those coins from that viewer. Example chips fill in common bets; the
   result shows what was accepted, rejected and debited.
5. **Hex display** — try the [Mode B display calls](#announce-and-board) by
   hand. For the payouts card: a title, the lines (one per line,
   `user amount text…`, e.g. `alice 200 Pass line`; the amount is optional,
   and `-` as the user means none), the empty text, seconds and an optional
   roll id, then **Announce** or **Clear**. For the bets board: a board title
   and a box of bets (`user amount text…` per line) with **Set board** and
   **Clear board**. It shows each request and reply; the mirror shows the
   result, exactly like the overlay. Nothing here touches the table's bets or
   the ledger.
6. **Ledger** — a live tail of every craps coin movement: seq, time, user,
   debit/credit, amount, reason and bet. Per-user session net, house net, a
   **Filter by user** (**Everyone** resets it) and **Reload**. The ledger is
   shared with roulette's table, so gaps in the seqs here are roulette
   events.
7. **History & stats** — the last 20 rolls as dice, a histogram of totals
   2–12 against what fair dice give, points set and made, seven-outs,
   naturals, craps, hands (played out, plus the rolls in the current one), the
   longest hand, and hard ways. **Clear history** clears the roll history (not
   the table or the ledger).
8. **Settings** — everything that isn't placement or looks: roll seconds,
   result seconds, cooldown, bet window, currency name, min and max bet, the
   odds limit, what the field pays on 12, the **Throw clip** and **Landing
   clip** (suggestions come from your soundboard library), the built-in dice
   sounds on/off and their **volume** (also in the Edit Mode editor), hide the
   table when idle, keep it on screen while bets are down, and auto-roll. **Save
   settings** stores them; **Revert** drops your changes.
9. **API** — the endpoint table (the announce, board, timer and shared
   ledger calls included), the bet syntax, curl examples built for this
   host, and the "Hooking up the bank" steps.

### Edit Mode — placement and appearance

Same as Roulette's: turn on **Edit Mode**, then click the Craps card (or its
**✎ Edit placement** button) to open the editor.

- A 16:9 preview of the 1920×1080 stage with the real tray. **Drag the tray**
  to move it; the position readout is the tray's centre, as a percentage of
  the canvas.
- **Scale** slider from `0.2` to `5` (× the 720×405 tray).
- The 3×3 **quick positions** pad snaps to the corners, edges and centre.
- **Appearance**, live in the preview: theme and dice style; **Table** (the
  point row & puck, the shooter caption); **History** (the strip and how many
  rolls); **Bets** (the bets board and how many viewers); **Payouts** (the
  winners board and how many winners); **Sound** (the built-in dice sounds and
  their volume).
- **▶ Preview** — a local demo roll inside the editor. Nothing is sent to the
  server or the stream.
- **Test in OBS** — a real `test` roll on the overlay using the editor's
  *unsaved* placement and appearance. Test rolls are animation only: no bets
  settle, nothing goes in the ledger or history, no clip cues.
- **Reset** — everything in the editor back to its default (`x` 50, `y` 50,
  scale 1.0, `classic`, `red` dice, every board on). Not saved until you
  press Save.
- **Cancel** / **×** / **Esc** closes without saving; **Save** stores
  placement and appearance and restyles every overlay live.

---

## Soundboard clip cues

In **Settings**, **Throw clip** (`roll_clip`) names a soundboard clip to fire
the moment the dice are thrown and **Landing clip** (`land_clip`) one to fire
when they land. Leave either empty for none. They fire through the
soundboard's own `/api/play/{name}`, so the clip's saved position, volume,
trim and cooldown apply, and they play on the **soundboard overlay**
(`/overlay`). **Test** rolls never fire cues.

The built-in dice sounds are separate: synthesized in the games overlay,
controlled by `sfx` and `sfx_volume` (**Sound** in the Edit Mode editor).

---

## Timeline of a roll

```
throw ──── roll seconds ────► landing ──── result seconds ────► result ends ── cooldown ──► ready
  │                             │                                  │
  roll_clip fires               land_clip fires                    tray hides (if hide when idle
  bets close                    bets settle: table updated,        and nothing keeps it up)
                                ledger credits written,            auto-roll countdown restarts
                                bets open again                    (if bets remain)
```

- At the **throw** the server has already rolled the dice and worked out
  every settlement — but nothing is **committed** yet. While the dice are in
  the air the table is frozen: new bets, take-downs and clears get **409**
  `bets_closed`. (A `test` roll doesn't freeze anything.)
- At **landing** the roll is committed: the table changes (bets paid, taken,
  moved, the puck), credits go into the ledger, the roll goes into history.
  Bets are accepted again straight away — during the result and cooldown too.
- While a roll is in the air, in its result phase or in cooldown, a new roll
  gets **409 busy**. With the defaults (4 s roll, 5 s result, no cooldown)
  that's one roll every 9 seconds at most.

---

## The game

### Come-out and point

The table is always in one of two **phases**:

- **Come-out** (`come_out`, puck OFF). The next roll is a come-out roll. A
  **7 or 11** is a **natural** — pass wins. **2, 3 or 12** is **craps** — pass
  loses. **4, 5, 6, 8, 9 or 10** becomes the **point** — the puck goes ON.
- **Point** (`point`, puck ON). Rolling the **point** again (**point made**)
  pays the pass line and goes back to a come-out. A **7** first (**seven out**)
  takes the pass line and goes back to a come-out. Any other total is just a
  roll — it matters to the other bets, not to the line.

The **shooter** is only a name for the caption — it doesn't change anything
about the dice or the odds. A roll's `user` becomes the shooter when there
isn't one yet. `hand_rolls` counts the rolls in the current hand. A **seven
out** ends the hand: the shooter is cleared (whoever rolls next shoots) and
`hand_rolls` starts again. **Refund all & clear table** (`/clear`) clears the
shooter too.

Every roll gets an `event` and a stickman `call`:

| Phase | Roll | `event` | After |
| --- | --- | --- | --- |
| come-out | 7, 11 | `natural` | still come-out |
| come-out | 2, 3, 12 | `craps` | still come-out |
| come-out | 4, 5, 6, 8, 9, 10 | `point_set` | point is that total |
| point | the point | `point_made` | come-out |
| point | 7 | `seven_out` | come-out |
| point | anything else | `roll` | unchanged |

| Roll | `call` — `sub` |
| --- | --- |
| 2, 3, 11, 12 (any phase) | `ACES`, `ACE-DEUCE`, `YO-LEVEN`, `BOXCARS` |
| 7 or 11 on a come-out | `SEVEN · WINNER` / `YO-LEVEN` — `Front line winner` |
| craps on a come-out | `ACES` / `ACE-DEUCE` — `Craps · line away`; `BOXCARS` — `Craps · bar the 12` |
| point set | `POINT IS 6` — `Easy six · mark it` / `Hard six · mark it` (`Five · mark it` on 5 and 9) |
| point made | `WINNER 6` — `Pay the line` |
| seven out | `SEVEN OUT` — `Line away · don't pass wins` |
| other point-phase rolls | `HARD EIGHT`, `EASY EIGHT`, `NINE`, `FIVE`, `ACE-DEUCE`, … — `Point is 6` |

The server writes these strings; they're for display. In code, match on
`event`, `total` and `dice`.

### Bet reference

Bet text is case-insensitive, and spaces, underscores, hyphens and
apostrophes are ignored — `Place 6`, `place_6` and `place-6` are all `place6`. Odds are net,
"X to 1": a winning 60 on place 6 pays 70 on top of the 60.

| Type | Say | When it can be made | Pays | House edge |
| --- | --- | --- | --- | --- |
| `pass` | `pass`, `passline`, `line`, `pl` | come-out only | 1 to 1 | 1.414% |
| `dont_pass` | `dontpass`, `don'tpass`, `dp` | come-out only | 1 to 1; 12 on the come-out pushes (bar 12) | 1.364% |
| `come` | `come` | point phase only | 1 to 1 | 1.414% |
| `dont_come` | `dontcome`, `dc` | point phase only | 1 to 1; 12 on its first roll pushes | 1.364% |
| odds on pass | `odds`, `odds:pass`, `passodds` | point phase, you have a pass bet | true odds: 4/10 2:1 · 5/9 3:2 · 6/8 6:5 | 0% |
| lay odds on don't pass | `odds:dontpass`, `layodds`, `dpodds` | point phase, you have a don't pass bet | 4/10 1:2 · 5/9 2:3 · 6/8 5:6 | 0% |
| odds on come N | `odds:N`, `odds:comeN`, `comeodds:N` | your come bet has travelled to N | true odds, as pass | 0% |
| lay odds on don't come N | `odds:dontcomeN`, `layodds:N`, `dcodds:N` | your don't come bet is on N | lay odds, as don't pass | 0% |
| `place` | `place6`, `place:6`, `p6`, or the bare number `4 5 6 8 9 10` | anytime (off on the come-out) | 4/10 9:5 · 5/9 7:5 · 6/8 7:6 | 4/10 6.667% · 5/9 4.0% · 6/8 1.515% |
| `hard` | `hard6`, `hard:6`, `h6`, `hardsix` — 4, 6, 8, 10 | anytime (off on the come-out) | 4/10 7:1 · 6/8 9:1 | 4/10 11.111% · 6/8 9.091% |
| `field` | `field` | anytime — one roll | 3, 4, 9, 10, 11 1:1 · 2 2:1 · 12 3:1 (or 2:1, see `field_12_pays`) | 2.778% (12 pays 3) · 5.556% (12 pays 2) |
| `any7` | `any7`, `anyseven`, `seven`, `big red`, bare `7` | anytime — one roll | 4 to 1 | 16.667% |
| `any_craps` | `anycraps`, `craps`, `ac` | anytime — one roll | 7 to 1 on 2, 3, 12 | 11.111% |
| `aces` | `aces`, `snakeeyes`, `2`, `two` | anytime — one roll | 30 to 1 | 13.889% |
| `ace_deuce` | `acedeuce`, `3`, `three` | anytime — one roll | 15 to 1 | 11.111% |
| `yo` | `yo`, `eleven`, `11` | anytime — one roll | 15 to 1 | 11.111% |
| `boxcars` | `boxcars`, `midnight`, `12`, `twelve` | anytime — one roll | 30 to 1 | 13.889% |
| `horn` | `horn` — amount must be a multiple of 4 | anytime — one roll | 2 or 12: 27 to 4 · 3 or 11: 3 to 1 | 12.5% |
| `ce` | `ce`, `c&e`, `crapseleven` — amount must be even | anytime — one roll | 2, 3, 12: 3 to 1 · 11: 7 to 1 | 11.111% |

`N` in the odds forms is the come bet's number, e.g. `odds:6`,
`odds:dontcome9`. A bet can instead carry `"target": "<bet_id>"` to put odds
behind that exact line or come bet. The house edges are exact when the
amount pays in whole coins — see [Amounts](#amounts-limits-and-rounding).

**Betting the same thing again adds to it** — same user, same type and
number (or odds on the same bet) — except that a pass or come bet's flat
amount can't be raised once it's a contract bet (see below). The ledger
records an add as reason `add`, odds as `odds`.

Check any bet text with `GET /games/api/craps/validate?bet=...` (or the
panel's **Check** button) before you trust it.

### How bets resolve

**Pass / don't pass** (made on a come-out):

- Come-out roll — **pass**: 7 or 11 wins 1:1, 2/3/12 loses, a point number
  sets the point and the bet stays. **Don't pass**: 2 or 3 wins 1:1, 12
  pushes (the stake comes back), 7 or 11 loses, a point number and it stays
  on the table.
- Point rolls — the point wins **pass** 1:1 plus its odds at true odds;
  **don't pass** loses, with its lay odds. A 7 does the opposite: pass loses
  with its odds, don't pass wins 1:1 plus its lay odds at lay odds. Any other
  total: no action on the line.
- A line bet comes down when it wins or loses; bet the line again for the next
  come-out.

**Come / don't come** (made while a point is on):

- The first roll after the bet is its own come-out: **come** — 7 or 11 wins,
  2/3/12 loses, 4–10 **travels** to that number. **Don't come** — 2 or 3
  wins, 12 pushes, 7 or 11 loses, 4–10 travels.
- A travelled come bet then wins 1:1 (plus its odds) when its number rolls
  before a 7, and loses on a 7. A travelled don't come bet is the reverse,
  with its lay odds.
- This goes on in both phases: after the point is made, travelled come bets
  stay on the table through the come-out roll.

**Place N** — works in the point phase only. When N rolls it pays (4/10 9:5,
5/9 7:5, 6/8 7:6) — the winnings are credited and **the bet stays up**. It
loses on a 7.

**Hard N** (4, 6, 8, 10) — works in the point phase only. The hard way (a
pair: 2+2, 3+3, 4+4, 5+5) wins 7:1 on 4/10 or 9:1 on 6/8 and **stays up**; the
easy way of N (any other combination) or a 7 loses it.

**One-roll bets** resolve on the very next roll and always come down: field,
any 7, any craps, aces, ace-deuce, yo, boxcars, horn, C&E.

- **Field**: 3, 4, 9, 10, 11 pay 1:1, 2 pays 2:1, 12 pays `field_12_pays`
  to 1 (3 by default, or 2); 5, 6, 7, 8 lose.
- **Horn** is four equal bets on 2, 3, 11 and 12 in one (so the amount is 4
  units): a 2 or 12 nets 27 for every 4 bet, a 3 or 11 nets 3:1, anything
  else loses.
- **C&E** is craps plus eleven (2 units): 2, 3 or 12 nets 3:1, 11 nets 7:1,
  anything else loses.

### Working and off

- **Place and hard bets are off on the come-out.** They stay on the table,
  but a come-out roll doesn't touch them — a come-out 7 doesn't take them.
  The table shows them with `working: false`.
- **Come odds are off on the come-out.** If a come bet is decided on a
  come-out roll, its flat bet plays and its **odds come back** — on a 7 the
  flat loses and the odds are returned (outcome `returned`); on its number the
  flat wins and the odds are returned.
- **Lay odds** (don't pass / don't come) are always working.
- Everything else works on every roll.

### Taking bets down

A bet can come off the table (and its coins go back) unless it's a
**contract bet**: a **pass** bet once the point is set, or a **come** bet once
it has travelled. Everything else can come down — don't bets (at the
player's disadvantage), odds any time, place and hard bets, and one-roll bets
before the roll. The table marks each bet `removable: true|false`. Nothing
can come down while the dice are in the air.

### Amounts, limits and rounding

Hexcoins are whole coins.

- Every amount must be a **whole number ≥ `min_bet`** (default 1).
- A bet's resulting flat amount (after adding to it) must be **≤ `max_bet`**
  (default 100 000; `0` = no maximum).
- Horn amounts must be a multiple of 4, C&E amounts even.

**Odds limits** follow `odds_rule`:

| `odds_rule` | Odds behind pass / come | Lay odds behind don't pass / don't come |
| --- | --- | --- |
| `"345"` (default) | 3× the flat on 4/10, 4× on 5/9, 5× on 6/8 | 6× the flat on every number |
| `"1"` `"2"` `"3"` `"5"` `"10"` `"20"` `"100"` (N) | N× the flat | up to what wins N× the flat: 2N× on 4/10, 1.5N× on 5/9, 1.2N× on 6/8, rounded down to whole coins |

With `345`, full odds always win 6× the flat bet: a 10 pass line can take 30
odds on a 4 (wins 60), 40 on a 5 (wins 60) or 50 on a 6 (wins 60), and a 10
don't pass can lay 60 on any number.

**Winnings are rounded down to whole coins** — a casino that doesn't pay
change. Place 6 with 10 would win 11.67, so it pays **11**; with 12 it pays
exactly 14. Stakes always come back in full; only the winnings round. So
amounts that pay exactly are the ones worth suggesting:

| Bet | Pays | Amounts that pay exactly |
| --- | --- | --- |
| Place 6 / 8 | 7:6 | multiples of 6 (6, 12, 30, 60 …) |
| Place 4 / 5 / 9 / 10 | 9:5 · 7:5 | multiples of 5 |
| Odds on 6 / 8 | 6:5 | multiples of 5 |
| Odds on 5 / 9 | 3:2 | even amounts |
| Odds on 4 / 10 | 2:1 | any |
| Lay odds on 4 / 10 | 1:2 | even amounts |
| Lay odds on 5 / 9 | 2:3 | multiples of 3 |
| Lay odds on 6 / 8 | 5:6 | multiples of 6 |
| Horn | 27:4 · 3:1 | multiples of 4 (required) |
| C&E | 3:1 · 7:1 | even (required) |
| Line, come, field, hard ways, props | whole odds | any |

`/validate` returns a `hint` when an amount won't pay exactly — a bot can pass
it straight to chat.

---

## HTTP API

All endpoints return JSON with `"ok": true` or `"ok": false` (plus an
`"error"`). Parameters go in the query string, a JSON body (POST), or both.
No authentication (LAN tool, like the rest of Hexcast).

| Endpoint | What it does |
| --- | --- |
| `GET\|POST /games/api/craps/roll` | throw the dice (aliases `/spin`, `/play`) |
| `GET\|POST /games/api/craps/bet` | put one or more bets on the table |
| `GET\|POST /games/api/craps/remove` | take bets down (refund) |
| `GET /games/api/craps/table` | the table: phase, point, shooter, every bet, exposure |
| `GET /games/api/craps/user/{name}` | one viewer's bets, exposure and session totals |
| `GET /games/api/craps/ledger?since=0&limit=500` | craps' ledger events after a seq |
| `GET /games/api/ledger?since=0&limit=500&game=` | every game's ledger events (craps and roulette), or one game's |
| `GET\|POST /games/api/craps/clear` | refund every bet and reset the table |
| `GET\|POST /games/api/craps/timer` · `timer/cancel` | start the countdown to the next roll now · stop it (see [Roll timer](#roll-timer)) |
| `GET /games/api/craps/validate?bet=&user=&amount=&target=` | dry-run one bet against the current table |
| `GET /games/api/craps/bets` | bet reference: types, syntax, pays, when, notes + rules |
| `GET /games/api/craps/last` | the last committed roll |
| `GET /games/api/craps/history?limit=20` | recent rolls, newest first |
| `POST /games/api/craps/history/clear` | clear roll history (GET works too) |
| `GET\|POST /games/api/craps/announce` · `announce/clear` | Mode B: a payouts card from Hex for the current or last roll · take it down (see [Announce and board](#announce-and-board)) |
| `GET\|POST /games/api/craps/board` · `board/clear` | Mode B: Hex's own bets board instead of the table's · back to the table's |
| `GET\|POST /games/api/craps/show` · `hide` | put the idle table on / off screen |
| `GET\|POST /games/api/craps/stop` | abort the animation and hide; a roll in the air is committed |
| `GET\|POST /games/api/stop` | the same for every game |
| `GET /games/api/status` · `GET\|POST /games/api/config` | shared with Roulette (see [Config](#config)) |
| `WS /games/ws/overlay`, `WS /games/ws/panel` | live updates (see [WebSocket](#websocket)) |

### Roll

`GET` or `POST /games/api/craps/roll` (also `/spin` and `/play`).

| Parameter | What it does |
| --- | --- |
| `user` | who's rolling — becomes the shooter if there isn't one |
| `wait` | `true` holds the response until the dice land and the roll is settled (default `false`) |
| `test` | `true` for an animation-only roll: no settlement, no ledger, no history, no clip cues — the table isn't touched and stays open for bets |
| `duration` | roll time in seconds, clamped to 2.5–10 (default: **roll seconds** from the config) |
| `bets` | JSON body: bets to place first, exactly like [`/bet`](#bet); the response carries `placed` |
| `overrides` | JSON body: appearance keys for this roll only (see [Config keys](#config-keys)) |
| `x`, `y`, `scale` | query shorthand for per-roll placement |

```
curl "http://localhost:4747/games/api/craps/roll?user=bob"
curl "http://localhost:4747/games/api/craps/roll?user=bob&wait=true"
curl -X POST http://localhost:4747/games/api/craps/roll -H "Content-Type: application/json" -d "{\"test\":true}"
```

Response (HTTP 200) — the **roll object**. Here alice has 100 on the pass
line with 50 odds, bob 50 on place 5 and carol 25 on the field, the point is
5, and the shooter rolls 1-4:

```json
{
  "ok": true,
  "id": "c-1a2b3c4d",
  "game": "craps",
  "result": {"dice": [1, 4], "total": 5, "pair": false, "hard": false,
             "number": "5", "label": "1-4",
             "event": "point_made", "call": "WINNER 5", "sub": "Pay the line",
             "phase_before": "point", "point_before": 5, "phase_after": "come_out", "point_after": null},
  "user": "bob",
  "shooter": "bob",
  "test": false,
  "seed": 123456789,
  "duration_ms": 4000,
  "result_ms": 5000,
  "started_at": 1790000000.123,
  "lands_at": 1790000004.123,
  "settlements": [
    {"bet_id": "b-1a2b3c4d", "user": "alice", "type": "pass", "label": "Pass line", "number": null,
     "amount": 100, "odds": 50, "outcome": "win", "won": 175, "credit": 325, "stays": false,
     "note": "Pass line 1:1 + odds 3:2", "lost": 0, "odds_returned": 0},
    {"bet_id": "b-5e6f7a8b", "user": "bob", "type": "place", "label": "Place 5", "number": 5,
     "amount": 50, "odds": 0, "outcome": "win", "won": 70, "credit": 70, "stays": true,
     "note": "Place 5 7:5 · stays up", "lost": 0, "odds_returned": 0},
    {"bet_id": "b-9c0d1e2f", "user": "carol", "type": "field", "label": "Field", "number": null,
     "amount": 25, "odds": 0, "outcome": "lose", "won": 0, "credit": 0, "stays": false,
     "note": "Field loses on 5", "lost": 25, "odds_returned": 0}
  ],
  "credits": [{"user": "alice", "amount": 325}, {"user": "bob", "amount": 70}],
  "summary": {"bets_settled": 3, "winners": 2, "total_won": 245, "total_lost": 25, "total_credited": 395},
  "table": {"phase": "come_out", "point": null, "...": "..."},
  "ledger": [{"seq": 57, "type": "credit", "user": "alice", "amount": 325, "reason": "win", "...": "..."},
             {"seq": 58, "type": "credit", "user": "bob", "amount": 70, "reason": "win_stays", "...": "..."}],
  "overrides": {},
  "landed": true
}
```

(`label`, `note`, `call` and `sub` are display text — match on `type`,
`number`, `outcome` and `event` in code.)

- `result` — the dice. `number` is the total as a string, `label` the dice
  (`"1-4"`), `pair` both dice equal, `hard` a pair on 4, 6, 8 or 10. `event`,
  `phase_before`/`point_before` and `phase_after`/`point_after` describe what
  the roll did to the table (see [Come-out and point](#come-out-and-point)).
- `settlements` — every bet that had **action** on this roll (bets the roll
  didn't touch aren't listed):
  - `outcome` — `win`, `lose`, `push`, `travel` (a come / don't come bet moved
    from the come box to a number: `travel_to` is that number, while `number`
    is still `null`, where the bet was when the dice were thrown) or
    `returned` (the flat bet lost, but its odds were off or are handed back).
  - `won` — net winnings (0 unless it won), already rounded down.
  - `credit` — coins due back now: a win pays stake + winnings, or the
    winnings only when the bet `stays` up (place, hard ways); a push pays the
    stake; `returned` pays the odds; a loss or a travel pays 0.
  - `lost` — coins the bet lost (the flat, plus odds that were working);
    `odds_returned` — odds handed back inside `credit` (come odds on a
    come-out, or odds behind a bet decided on its own come-out roll).
- `credits` — the same coins added up per user, for the chat announcement.
  Hex pays them through the ledger (see [Hooking up the bank](#hooking-up-the-bank-hexcoins)).
- `summary` — for a one-line chat recap.
- `table` — with `wait=true`, the table **after** the roll. Without `wait`,
  the table as it was before the roll.
- `ledger` — with `wait=true`, the ledger events this roll wrote (the same
  events `/ledger` serves).
- `placed` — only when the roll carried `bets`: the `/bet`-style result
  (`accepted`, `rejected`, `debits`) for them.
- `landed: true` — only with `wait=true`. If `/stop` hits while the dice are
  in the air, the waiting response comes back right then with
  `"landed": false, "stopped": true` — the roll still counts and is settled.

**Settlements are worked out at the throw but only committed at landing.**
Without `wait` the response comes back instantly with the dice, the
settlements and the credits in it — don't announce them before `lands_at`,
and **don't pay from it**: nothing is due until the dice land, and a restart
while they're in the air cancels the roll. Pay from the [ledger](#ledger).
With `wait=true`, give your HTTP client a timeout longer than the roll (up to
10 s).

**Busy.** One roll at a time. While the dice are in the air, in the result
phase or in cooldown, a roll gets **HTTP 409**:

```json
{"ok": false, "error": "busy", "retry_in_ms": 6240, "state": "result"}
```

`state` is `spinning` (the dice are in the air — the name is shared with
Roulette), `result` or `cooldown`; `retry_in_ms` is how long until a roll
will be accepted.

### Bet

`GET` or `POST /games/api/craps/bet`. One bet as `user`, `bet`, `amount` (and
optional `target`), or a list as `{"bets": [...]}` (up to 200).

```
curl "http://localhost:4747/games/api/craps/bet?user=alice&bet=pass&amount=100"
curl "http://localhost:4747/games/api/craps/bet?user=alice&bet=odds&amount=200"
curl -X POST http://localhost:4747/games/api/craps/bet -H "Content-Type: application/json" -d "{\"bets\":[{\"user\":\"bob\",\"bet\":\"place6\",\"amount\":60},{\"user\":\"carol\",\"bet\":\"field\",\"amount\":25}]}"
```

URL-encode bet text in a query string — `c&e` is `c%26e`, a space is `%20`.

```json
{
  "ok": true,
  "accepted": [
    {"id": "b-1a2b3c4d", "user": "alice", "type": "pass", "number": null, "amount": 100, "odds": 0,
     "label": "Pass line", "working": true, "removable": true, "one_roll": false,
     "placed_at": 1790000000.1, "odds_working": true}
  ],
  "rejected": [],
  "debits": [{"user": "alice", "amount": 100, "bet_id": "b-1a2b3c4d", "seq": 42, "reason": "bet"}],
  "table": {"...": "..."}
}
```

- `accepted` — the accepted bets, as [BET](#table) objects (odds or an add
  show the line bet with its new totals). A bet whose amount won't pay
  exactly also carries a `hint`.
- `rejected` — `{user, bet, amount, error}` for each bet that wasn't taken;
  `error` is written for chat (e.g. `odds limit (3-4-5x): at most 300 more -
  500 max behind 100 on 6, 200 already down`). **Nothing is debited for a
  rejected bet.**
- `debits` — one entry per accepted bet: the coins Hex takes now, with
  the ledger `seq` and `reason` (`bet`, `add`, `odds`) of that debit.
- HTTP **200** when anything was accepted (or nothing was sent), **400** when
  every bet was rejected. The body's `error` then holds the reason (or
  `every bet was rejected` when there were several).
- HTTP **409** `{"ok": false, "error": "bets_closed", "retry_in_ms": N}` while
  the dice are in the air — nothing was placed; try again after `retry_in_ms`.

### Remove

`GET` or `POST /games/api/craps/remove` — take bets down. The coins come back
as credits (reason `remove`).

| Body / query | Takes down |
| --- | --- |
| `{"bet_id": "b-1a2b3c4d"}` | that bet (flat + odds) |
| `{"user": "bob", "bet": "place6"}` | that user's bet, named with the usual bet text |
| `{"user": "bob", "bet": "odds"}` | only the odds behind bob's pass line (`odds:6`, `layodds`, … work the same) — also on a contract bet |
| `{"user": "bob", "all": true}` | everything of bob's that can come down, plus the odds behind his contract bets |

Response: `{"ok": true, "removed": [BET, ...], "credits": [{"user", "amount"}], "ledger": [...], "table": {...}}`
— each removed entry also has `refund` (coins back) and `odds_only`.
Taking down a contract bet (pass after the point, a travelled come bet), or a
bet the user doesn't have, answers **400** with an `error` saying why (`Pass
line is a contract bet once the point is set - it can't come down`). While
the dice are in the air it's **409** `bets_closed`.

```
curl "http://localhost:4747/games/api/craps/remove?user=bob&bet=place6"
```

### Table

`GET /games/api/craps/table` → `{"ok": true, "table": TABLE}`. The same
`table` rides along in `/bet`, `/remove`, `/clear` and `/roll` responses and
in every websocket `state` message.

```json
{
  "phase": "point", "point": 6, "shooter": "bob", "hand_rolls": 3,
  "bets": [
    {"id": "b-1a2b3c4d", "user": "alice", "type": "pass", "number": null, "amount": 100, "odds": 200,
     "label": "Pass line", "working": true, "removable": false, "one_roll": false,
     "placed_at": 1790000000.1, "odds_working": true}
  ],
  "exposure": {"alice": 300}, "total_on_table": 300,
  "bets_open": true, "auto_roll_in_ms": 12000, "last_seq": 43, "currency": "hexcoins",
  "min_bet": 1, "max_bet": 100000, "odds_rule": "345", "field_12_pays": 3,
  "display_board": null
}
```

| Field | Meaning |
| --- | --- |
| `phase` | `come_out` or `point` |
| `point` | the point number, `null` during the come-out |
| `shooter` | the shooter's name, or `null` |
| `hand_rolls` | rolls in the current hand |
| `bets` | every bet on the table |
| `exposure` | coins each user has on the table (amount + odds) |
| `total_on_table` | all of it added up |
| `bets_open` | `false` only while the dice are in the air |
| `auto_roll_in_ms` | time until the countdown rolls — [auto-roll](#auto-roll)'s or the [roll timer](#roll-timer)'s — measured when the reply was built; `null` when none is running |
| `last_seq` | the newest ledger `seq` — of any game, since the [ledger](#ledger) is shared with roulette |
| `currency`, `min_bet`, `max_bet`, `odds_rule`, `field_12_pays` | from the config |
| `display_board` | Mode B: the board Hex posted with [`/board`](#announce-and-board), `{"title", "bets", "total"}`. `null` when there isn't one — the bets board then shows `bets` |

A **BET**:

| Field | Meaning |
| --- | --- |
| `id` | `b-…`, stable for the bet's life on the table |
| `user` | whose it is |
| `type` | `pass`, `dont_pass`, `come`, `dont_come`, `place`, `hard`, `field`, `any7`, `any_craps`, `aces`, `ace_deuce`, `yo`, `boxcars`, `horn`, `ce` |
| `number` | place / hard: its number · come / don't come: `null` in the come box, then the number it travelled to · pass / don't pass: `null` (the table's `point` applies) |
| `amount` | the flat bet |
| `odds` | odds behind a pass / don't pass / come / don't come bet (0 if none) |
| `label` | readable name for chat |
| `working` | `false` while the bet is off (place, hard and come odds on a come-out) |
| `removable` | whether it can be taken down (see [Taking bets down](#taking-bets-down)) |
| `one_roll` | `true` for the one-roll bets |
| `placed_at` | server epoch seconds |
| `odds_working` | line bets only: `false` while the odds behind it are off (a come bet on a come-out), else `true`; `null` for other bets |

The table (phase, point, shooter, hand rolls and every bet) is saved to disk
after every change and restored when Hexcast starts, so bets survive a
restart. `display_board` isn't saved: it's `null` again after a restart.

### User

`GET /games/api/craps/user/{name}` →

```
{"ok": true, "user": "alice", "bets": [BET], "exposure": 100,
 "session": {"debits": 400, "credits": 640, "net": 240, "events": 4}}
```

(alice from the [hand below](#a-chat-command-flow): pass 100 + odds 200 won 640,
then another 100 on the pass line.) `session` adds up that user's craps events in the ledger Hexcast keeps in memory
(the latest 10 000, of every game): `debits`, `credits`, `net` = credits − debits (coins on
the table count as spent until they come back), and the event count. Handy
for a `!mybets` reply. It isn't a balance; Hex's hexbank owns balances.

### Ledger

`GET /games/api/craps/ledger?since=41&limit=500` →

```json
{"ok": true, "events": [
  {"seq": 42, "ts": 1790000000.123, "game": "craps", "type": "debit", "user": "alice", "amount": 100,
   "reason": "bet", "bet_id": "b-1a2b3c4d", "bet": "Pass line", "roll_id": null},
  {"seq": 43, "ts": 1790000031.402, "game": "craps", "type": "credit", "user": "alice", "amount": 200,
   "reason": "win", "bet_id": "b-1a2b3c4d", "bet": "Pass line", "roll_id": "c-5f6e7d8c"}
 ], "last_seq": 43, "truncated": false, "oldest_seq": 1}
```

(alice's 100 pass line won 1:1 on a come-out 7: 100 stake + 100 won.)

**One ledger, every game.** Roulette's
[table](games.md#table--spin-timer) writes to the same ledger: one file, one
`seq` numbering, and every event names its game in `game` (`craps` or
`roulette`). `/games/api/craps/ledger` serves only the craps events, so its
seqs have gaps where roulette's are — that's normal. `GET /games/api/ledger`
serves every game's events in one stream (add `&game=craps` or
`&game=roulette` for one; an unknown name is a 404 `unknown game`), with the
same parameters and reply — a bank that runs both tables tails that once.

```
curl "http://localhost:4747/games/api/craps/ledger?since=0"
curl "http://localhost:4747/games/api/ledger?since=0&game=craps"
```

- Returns the events with `seq` **greater than** `since`, oldest first, at
  most `limit` of them (default 500, up to 5000). `last_seq` is the ledger's
  newest seq, of any game; `oldest_seq` the oldest one still held in memory,
  of any game.
- `truncated` is `true` when you didn't get everything after `since`: either
  `limit` cut it short (call again from the last seq you processed), or some
  of those events are older than the 10 000 (of every game) kept in memory
  (`oldest_seq` is higher than `since + 1` — read the file for those, see
  [Restarts](#restarts)).
- `seq` is unique across the games and only ever goes up, and it keeps
  counting across restarts.
- `type` is `debit` (coins Hex takes) or `credit` (coins Hex pays).
  `reason` says why:

| `type` | `reason` | When |
| --- | --- | --- |
| debit | `bet` | a new bet went on the table |
| debit | `add` | more was added to an existing bet |
| debit | `odds` | odds were put behind a line or come bet |
| credit | `win` | a bet won and came down: stake + winnings |
| credit | `win_stays` | a bet won and stays up (place, hard ways): winnings only |
| credit | `push` | a push: the stake back |
| credit | `returned` | off come odds handed back on a come-out |
| credit | `remove` | a bet was taken down |
| credit | `refund` | the table was cleared |

- `bet` is a readable label (`Pass line`, `Pass line odds`, `Come 6 lay
  odds`, …). `roll_id` is the roll's `id` for events a roll caused, `null`
  otherwise.
- Every event (of every game) is also appended as one JSON line to
  `config/games_ledger.jsonl` the moment it happens (rotated to
  `games_ledger.jsonl.1` at 10 MB). The API serves the most recent 10 000,
  reloaded from the file when Hexcast starts.
- Panel websockets get each new batch pushed as
  `{"type": "ledger", "game": "craps", "events": [...]}` — one message per
  game, so this one only ever holds craps events (roulette's come as
  `"game": "roulette"`).

### Clear

`GET` or `POST /games/api/craps/clear` — refunds every bet on the table
(credits, reason `refund`), resets to a come-out, clears the shooter and
stops any countdown.
Returns `{"ok": true, "credits": [...], "ledger": [...], "refunded": N, "table": {...}}`.
409 `bets_closed` while the dice are in the air. The panel's **Refund all &
clear table** does the same.

### Validate and the bet reference

`GET /games/api/craps/validate?bet=...&user=...&amount=...&target=...` —
checks one bet against the **current** table without placing it (nothing
moves).

```
curl "http://localhost:4747/games/api/craps/validate?bet=place6&amount=25"
curl "http://localhost:4747/games/api/craps/validate?bet=odds&user=alice&amount=200"
```

```json
{"ok": true, "bet": "place6", "valid": true, "type": "place", "label": "Place 6", "number": 6,
 "odds_text": "7:6", "action": "new", "user": null, "amount": 25,
 "hint": "Place 6 pays 7:6: bet multiples of 6 to be paid in full (25 wins 29 - winnings round down)"}
```

It always has `valid`, `type`, `label`, `number`, `odds_text`, and the
`user` and `amount` it checked. It adds `error` when the bet isn't valid
(still `"ok": true`; an unknown bet has `type` `invalid`), a `hint` when the
amount won't pay exactly, and `action` (`new`, `add` or `odds`) when it would
be accepted. For odds, `type` is `odds` or `lay_odds`, with the bet it would
go behind in `on` (its type) and `target` (its id), `max_odds` (how much more
fits behind it now) and `odds_limit` (the total limit behind it).
Pass `user` and `amount` so it can check what depends on them: odds need
that user's line or come bet, and the odds limit comes from its flat amount.

`GET /games/api/craps/bets` returns the whole bet reference —
`bets: [{type, label, syntax[], pays, when, one_roll, note}]`, the `rules`
text, and the table's `currency`, `min_bet`, `max_bet`, `odds_rule` and
`field_12_pays` — so a bot can build its own `!craps` help.

### History, show, hide, stop

These work exactly like Roulette's ([Games → Show, hide, stop](games.md#show-hide-stop)):

- `/last` — the last committed roll; `/history?limit=20` — recent rolls,
  newest first, plus `stats`: `rolls`, `counts` per total (`"2"` … `"12"`),
  `points_set`, `points_made`, `seven_outs`, `naturals`, `craps`,
  `hard_ways`, `hands`, `longest_hand`, `current_hand`; `/history/clear` —
  clears the roll history and stats (not the table, not the ledger). History
  lives in memory — up to 200 rolls, gone on restart. Test rolls are never
  recorded.
- `/show` puts the idle table on screen; `/hide` takes it off (409 while the
  dice are in the air). With **show when bets** on, the table also stays up
  on its own while any bet is on it (or Hex's [board](#announce-and-board)
  has lines) — `/hide` still hides it.
- A running countdown — [auto-roll](#auto-roll) or the
  [roll timer](#roll-timer) — keeps the table on screen too, even with
  **show when bets** off and **hide when idle** on.
- `/stop` (and `/games/api/stop`, the panel's **⏹ Stop**) aborts the
  animation and hides the table. A roll in the air is **committed** — it's
  settled and paid exactly as if it had landed — and any countdown is
  cancelled.
- After `/hide` or `/stop` the table stays hidden — even with **show when
  bets** on and bets down, and while a countdown runs — until `/show`, a
  `/timer` or the next roll (a new bet with auto-roll on starts a fresh
  countdown, and that roll brings it back).

### Announce and board

The display calls for [Mode B](#two-ways-to-run-the-money), where Hex keeps
the bets and does the math. Hex tells the overlay what to **show**; nothing
here touches the dice, the table's bets, the ledger, the history or the
stats.

#### Announce — the payouts card

`GET` or `POST /games/api/craps/announce` puts a payouts card up for the
current (or last) roll. On the tray it **replaces the payouts board** for
that roll, in the same style: the title, up to **payouts max** lines (then
`+N more`), each with `@user`, the text and the amount (`+` green, `−` red,
`0` dim) followed by the currency — or `empty_text` when there are no lines.
**show payouts** applies to it too: with it off, no card is shown. It's the
same call as [Roulette's](games.md#announce). Parameters go in the query
string, a JSON body (POST), or both — the body wins. A `POST` body:

```json
{
  "spin_id": "c-1a2b3c4d",
  "title": "PAYOUTS",
  "lines": [
    {"user": "alice", "amount": 200, "text": "Pass line"},
    {"user": "carol", "amount": 0, "text": "Don't pass (push)"},
    {"user": "bob", "amount": -50, "text": "Field"}
  ],
  "empty_text": "No action",
  "seconds": 8
}
```

```
curl "http://localhost:4747/games/api/craps/announce?user=alice&amount=200&text=Pass%20line"
curl -X POST http://localhost:4747/games/api/craps/announce -H "Content-Type: application/json" -d "{\"lines\":[{\"user\":\"alice\",\"amount\":200,\"text\":\"Pass line\"},{\"user\":\"bob\",\"amount\":-50,\"text\":\"Field\"}],\"seconds\":8}"
curl http://localhost:4747/games/api/craps/announce/clear
```

(The curls leave out `spin_id` so they work as they are; Hex sends the id of
the roll it's announcing.)

| Parameter | Default | What it does |
| --- | --- | --- |
| `lines` | — | the card's lines, `[{"user", "amount", "text"}]` (a single line object works too; in a query string, as JSON text) — up to 50 are kept, the rest dropped. Every field is optional, but a line needs a `user` or a `text`; a line that isn't usable (neither of those, or a bad `amount`) is skipped. `[]` means nobody won: the card shows `empty_text` |
| — `user` | — | who. Cleaned like a roll's `user`: control characters removed, a leading `@` dropped, up to 40 characters |
| — `amount` | — | a finite number, at most 1 000 000 000 000 either way (`1e12`). Positive shows as `+200` (green), negative as `−50` (red), `0` as `0` (dim). Leave it out for no amount |
| — `text` | — | what for (`Pass line`), up to 60 characters, control characters removed |
| `user`, `amount`, `text` | — | query shorthand for one line: `?user=alice&amount=200&text=Pass%20line` (added after any `lines`) |
| `title` | `"WINNERS"` | the card's title, up to 40 characters |
| `empty_text` | `"No winners"` | shown instead of lines when there are none, up to 60 characters |
| `currency` | the craps `currency` setting (`"hexcoins"`) | shown after each amount, up to 24 characters; `""` for none |
| `seconds` | the rest of the result phase (all of it while the dice are in the air), else **result seconds** | 1–120 — how long the card stays up. Posted while the dice are in the air, it counts from the landing |
| `spin_id` | the current roll (in the air or showing its result), else the last committed one | which roll the card is about — it has to be that same roll |

Response (HTTP 200) — the card as the ANNOUNCE object, plus the craps STATE:

```json
{
  "ok": true,
  "announce": {"id": "a-1a2b3c4d", "spin_id": "c-1a2b3c4d", "title": "PAYOUTS",
               "lines": [{"user": "alice", "amount": 200, "text": "Pass line"},
                         {"user": "carol", "amount": 0, "text": "Don't pass (push)"},
                         {"user": "bob", "amount": -50, "text": "Field"}],
               "empty_text": "No action", "currency": "hexcoins", "expires_in_ms": 8000},
  "state": {"state": "result", "visible": true, "announce": {"...": "..."}, "table": {"...": "..."}, "...": "..."}
}
```

- The same object is in every craps STATE as `announce` (`null` when there's
  no card). `lines` always carry all three keys (`null` for what wasn't
  sent); `expires_in_ms` is how long the card has left, measured when the
  message was built.
- **Timing.** Post it any time — straight after a `wait=true` roll comes back
  is the usual moment. Posted while the dice are in the air, it's held until
  they land, so it never spoils the roll; its `seconds` then count from the
  landing.
- **How long.** It stays up for `seconds`, and the tray stays on screen that
  long too, even with **hide when idle** on. `/hide` and `/stop` still take
  the tray off screen, card and all — but they don't clear the card, so a
  `/show` before its time is up brings it back. When the time is up the
  server takes it down and every overlay and panel updates.
- **Clearing.** The next roll clears it; posting again replaces it.
  `GET|POST /games/api/craps/announce/clear` clears it now and answers
  `{"ok": true, "cleared": true|false, "announce": null, "state": STATE}`.
  Cleared while the result is still up, the payouts board Hexcast built
  comes back (if any bets on its table had action).
- **`spin_id`** must be the id of the current roll (in the air or showing its
  result) or, when there isn't one, of the last committed roll. Anything else
  gets **HTTP 409**
  `{"ok": false, "error": "stale", "spin_id": "<the id it expected>"}`
  (`null` when there's none: no roll since startup or since the last
  history clear) and nothing is shown. Without `spin_id` the card goes with
  the current roll, or else the last one.
- **HTTP 400** when the body isn't valid JSON, when `lines` isn't a list
  of lines, when neither `lines` (nor the one-line shorthand) nor
  `empty_text` was sent, or when every line sent was unusable and there's
  no `empty_text`.
  `"lines": []` on its own is fine — it shows `No winners`.

#### Board — the bets that are down

`GET` or `POST /games/api/craps/board` replaces the **bets board** — the
between-rolls list of who has what down — with Hex's own lines. A `POST`
body:

```json
{
  "title": "ON THE TABLE",
  "bets": [
    {"user": "alice", "text": "Pass 100 + odds 50", "amount": 150},
    {"user": "bob", "text": "Place 6 60, Field 25", "amount": 85}
  ]
}
```

```
curl -X POST http://localhost:4747/games/api/craps/board -H "Content-Type: application/json" -d "{\"bets\":[{\"user\":\"alice\",\"text\":\"Pass 100 + odds 50\",\"amount\":150},{\"user\":\"bob\",\"text\":\"Place 6 60, Field 25\",\"amount\":85}]}"
curl -X POST http://localhost:4747/games/api/craps/board -H "Content-Type: application/json" -d "{\"bets\":[]}"
curl http://localhost:4747/games/api/craps/board/clear
```

| Field | Default | What it does |
| --- | --- | --- |
| `bets` | — | the board's lines, `[{"user", "text", "amount"}]` (a single line object works too; in a query string, as JSON text) — up to 100 are kept, the rest dropped. `user` up to 40 characters (cleaned like a roll's user), `text` up to 60, `amount` optional, a finite number from 0 to 1 000 000 000 000. A line needs a `user` or a `text`; a line that isn't usable is skipped. `[]` = nothing down (no board) |
| `user`, `amount`, `text` | — | query shorthand for one line (added after any `bets`) |
| `title` | `"ON THE TABLE"` | the board's title, up to 40 characters |
| `clear` | — | `true` does the same as `/board/clear` (everything else is ignored) |

Response: `{"ok": true, "table": TABLE}`. The board is in the table as
`display_board`, with the cleaned lines (all three keys, `null` for what
wasn't sent) and `total`, the sum of the amounts:

```json
{
  "ok": true,
  "table": {
    "phase": "come_out", "point": null, "...": "...",
    "display_board": {"title": "ON THE TABLE",
                      "bets": [{"user": "alice", "amount": 150, "text": "Pass 100 + odds 50"},
                               {"user": "bob", "amount": 85, "text": "Place 6 60, Field 25"}],
                      "total": 235}
  }
}
```

- While `display_board` isn't `null`, the overlay's bets board shows it
  **instead of** the bets on Hexcast's table: the title, up to **bets max**
  lines (then `+N more`), each with its `@user`, amount and text, and the
  total. `{"bets": []}` is an empty board — Hex has nothing down — so no
  bets board is shown (and Hexcast's own stays away too). **show bets**
  applies to it, as to the built-in board.
- `GET|POST /games/api/craps/board/clear` (or `{"clear": true}`) sets it back
  to `null` — the board built from Hexcast's own table again. It answers
  `{"ok": true, "cleared": true|false, "table": TABLE}` (`false` if there was
  no board).
- **show when bets** counts it: while Hex's board has lines, the tray stays
  on screen between rolls, just as it does for bets on Hexcast's table.
- Post it any time, even while the dice are in the air — the overlay holds
  table updates until they land, so it never gives the roll away.
- It lives in memory only. After a Hexcast restart `display_board` is `null`
  again, so Hex should post its board again (check `table.display_board`
  when it reconnects).
- Every change is pushed to the overlays and panels as a `state` message.
- **HTTP 400** when the body isn't valid JSON, when `bets` isn't a list
  of lines, when there's no `bets` (nor the one-line shorthand) and no
  `clear`, or when every line sent was unusable. Nothing changes then.

### Config

The craps settings live in the `"craps"` section of the games config:

```
curl http://localhost:4747/games/api/config
curl -X POST http://localhost:4747/games/api/config -H "Content-Type: application/json" -d "{\"craps\":{\"min_bet\":10,\"auto_roll\":true,\"bet_window_seconds\":30}}"
```

`POST` merges what you send, validates, saves, pushes it to every overlay and
panel, and returns the full config. Unknown keys are dropped, numbers are
clamped to their range, an unknown choice falls back to its default.

#### Config keys

Keys marked *per roll* are the **appearance** keys — the only ones allowed in
a roll's `overrides`, and what the Edit Mode editor edits.

| Key | Default | Values | Per roll |
| --- | --- | --- | --- |
| `x`, `y` | `50`, `50` | 0–100 — tray centre, % of the 1920×1080 canvas | ✓ |
| `scale` | `1.0` | 0.2–5 — × the 720×405 tray | ✓ |
| `theme` | `"classic"` | `classic` \| `neon` \| `midnight` \| `royal` | ✓ |
| `dice_style` | `"red"` | `red` \| `white` \| `black` \| `gold` | ✓ |
| `roll_seconds` | `4` | 2.5–10 — throw to landing | |
| `result_seconds` | `5` | 1–120 — how long the call and payouts stay up | |
| `cooldown_seconds` | `0` | 0–3600 — extra lockout after the result phase (`0` = none) | |
| `hide_when_idle` | `true` | `false` keeps the tray on screen all the time | |
| `show_when_bets` | `true` | keep the tray on screen while any bet is on the table, or Hex's `/board` has lines (even with hide when idle) | |
| `show_point` | `true` | the point row and puck | ✓ |
| `show_history` | `true` | the strip of recent rolls | ✓ |
| `history_count` | `10` | 1–20 — rolls in the strip | ✓ |
| `show_user` | `true` | the shooter caption | ✓ |
| `show_bets` | `true` | the bets board (Hex's [`/board`](#announce-and-board) too) | ✓ |
| `bets_max` | `6` | 1–20 — viewers listed on the bets board (lines, on Hex's board) | ✓ |
| `show_payouts` | `true` | the payouts board after a roll (Hex's [`/announce`](#announce-and-board) card too) | ✓ |
| `payouts_max` | `5` | 1–20 — winners listed (lines, on Hex's card) | ✓ |
| `show_rules` | `true` | the "how to play" box while no roll is up: what the coming roll means (the come-out, or "the point is 6: roll a 6 before a 7"), then the bets, one line each, and what they pay — in the theme's colours, beside the tray on the side the payouts board uses after a roll | ✓ |
| `sfx` | `true` | built-in synthesized dice sounds in the overlay | ✓ |
| `sfx_volume` | `0.5` | 0–1 | ✓ |
| `roll_clip`, `land_clip` | `""` | soundboard clip names fired at the throw / landing (`""` = none; up to 200 chars) | |
| `currency` | `"hexcoins"` | 1–24 characters — the name on the payouts board and in the table | |
| `min_bet` | `1` | whole number, 1–1 000 000 000 | |
| `max_bet` | `100000` | whole number, 0–1 000 000 000 000; `0` = no maximum; a value below `min_bet` is reset to the default (or to `0` if `min_bet` is above that) | |
| `odds_rule` | `"345"` | `"345"` \| `"1"` \| `"2"` \| `"3"` \| `"5"` \| `"10"` \| `"20"` \| `"100"` (see [Amounts](#amounts-limits-and-rounding)) | |
| `field_12_pays` | `3` | `2` or `3` — the field pays this to 1 on a 12 | |
| `auto_roll` | `false` | roll on its own when bets are down (see [Auto-roll](#auto-roll)) | |
| `bet_window_seconds` | `20` | 5–300 — the auto-roll countdown, and the [roll timer](#roll-timer)'s default | |

---

## WebSocket

Same sockets and message types as Roulette (see
[Games → WebSocket protocol](games.md#websocket-protocol)), with two
additions:

- Craps' `{"type": "state", "game": "craps", "state": STATE}` carries the
  [table](#table) as `state.table` (with Hex's board, if any, as
  `state.table.display_board`). Like every game's STATE it also has
  `announce`, the [payouts card](#announce-and-board) Hex posted, or `null`.
- **Panel** sockets (`/games/ws/panel`) also get
  `{"type": "ledger", "game": "craps", "events": [...]}` for every new batch
  of craps ledger events — and `"game": "roulette"` messages for roulette's,
  since the [ledger](#ledger) is shared: one message per game, never mixed.
  Overlays never get it (and ignore message types they don't know).

---

## Two ways to run the money

Hexcast never holds anyone's hexcoins — **Hex** does, in the **hexbank**
inside its own code. The choice is **who does the bet math**:

| | **Mode A — Hexcast does the math** | **Mode B — Hex does the math** |
| --- | --- | --- |
| The bets live on | Hexcast's table (`/bet`) — across rolls, saved to disk | Hex's own code |
| Bets are settled by | Hexcast, by standard casino rules (`settlements`) | Hex's own code, from the roll's `result` |
| Coins are moved by | Hex's hexbank: the `/bet` reply's `debits`, then every credit in the [ledger](#ledger) | Hex's hexbank, however Hex's code decides |
| The bets board shows | the bets on Hexcast's table | the board Hex posts with [`/board`](#announce-and-board) |
| The payouts board shows | the settlements | the card Hex posts with [`/announce`](#announce-and-board) |
| Dice, puck, shooter, history | **Hexcast — fair dice** | **Hexcast — fair dice** |

The last row is the same either way. Hex can't send dice — there's no
parameter for them — so in Mode B Hex only tells the overlay what to
**show**. The display calls never touch the dice, the table's bets, the
ledger, the history or the stats.

**Pick Mode A** for standard bank craps with no rules to write in Hex.
Hexcast checks every bet, keeps it across rolls and restarts, settles it
(odds limits, working and off, rounding and all), and the ledger makes every
payout happen exactly once; take-downs, [auto-roll](#auto-roll) and the
panel's **On the table** and **Ledger** cards all work. Hex's side is the
debits and the ledger tail — see
[Hooking up the bank](#hooking-up-the-bank-hexcoins).

**Pick Mode B** when Hex already has craps code of its own, or wants rules
Hexcast doesn't have — different payouts, bonus or side bets, limits per
viewer. Then Hex owns everything the table does in Mode A: which bets are
allowed when, settling each one, keeping its bets safe across its own
restarts, and keeping the screen in step with `/board` and `/announce`.
Hexcast's table stays empty, so no craps events go in the ledger, auto-roll
never starts (it only counts bets on Hexcast's table — the
[roll timer](#roll-timer) gives Mode B a countdown), and the panel's **On the
table** and **Ledger** cards stay empty too. See
[Hex does the math](#hex-does-the-math-mode-b).

Use one mode per table: never also place Hex's bets with `/bet`, or Hexcast
would settle them and put the payouts in the ledger as well.

---

## Hooking up the bank (hexcoins)

This is **Mode A** — Hexcast runs the table and does the math (for Mode B,
see [Hex does the math](#hex-does-the-math-mode-b)).

The bank is **Hex**, the channel's bot. Hex's own code keeps everyone's
hexcoins in its **hexbank**; Hexcast keeps none. Hex takes the chat commands,
calls the Craps API, and moves the coins in its hexbank. This section is for
whoever writes Hex's side.

`hexbank.balance(user)`, `hexbank.debit(user, amount)` and
`hexbank.credit(user, amount)` below are **placeholder names** — use whatever
Hex's hexbank really calls them.

The short version:

- **Placing a bet** — Hex takes exactly the coins listed in the `/bet`
  reply's `debits[]`, and nothing for a rejected bet.
- **Paying out** — Hex applies credits by **tailing the ledger**
  (`/ledger?since=<last applied seq>`) and saves the last `seq` it applied.
  Every payout then happens exactly once, whoever rolled and whichever side
  restarts.
- **Announcing** — the `/roll?wait=true` reply's `credits[]` is for the chat
  message. Hex never pays from it.
- **Roulette's table too?** It writes to the same ledger, with the same
  `debits` and the same kind of credits. Tail `/games/api/ledger` (every
  game) instead of `/games/api/craps/ledger`, with the same `last_seq` and
  `done` — one tail pays both games. See
  [Games → Hex's side: the table](games.md#hexs-side-the-table-mode-a).

### Who does what

- **Hexcast** runs the table: it checks bets are legal, holds them across
  rolls, rolls the dice, settles, and writes every coin movement to the
  ledger with a unique, increasing `seq`.
- **Hex** holds the balances in its hexbank. It checks a viewer can afford a
  bet, takes the coins, pays the credits, and remembers how far through the
  ledger it is.
- Hexcast never sees a balance and never refuses a bet for lack of coins —
  that check is Hex's job.

Hexcast keys everything on the `user` string Hex sends. It drops a leading
`@` and keeps up to 40 characters, but it doesn't change case — so always
send the same form of a name (the viewer's lowercase login is a good choice).
Otherwise one viewer's bets won't group together, and a ledger event's `user`
won't match the hexbank's.

### What Hex saves

Next to the hexbank, Hex keeps a small record of how far through the ledger
it is:

| Field | What |
| --- | --- |
| `last_seq` | the `seq` of the last ledger event Hex has processed |
| `done` | seqs above `last_seq` that Hex already handled straight from a reply (the debits it took while placing bets, and the refund of a bet it took back down), so the ledger tail skips them. Each one drops out as the tail passes it, so the set stays tiny |
| `hold` | ordering (b) only: the coins taken for a bet whose reply Hex hasn't dealt with yet |

Save this record **in the same transaction as the hexbank change it goes
with** — the same database commit, or the same file write. Then a crash at
any moment leaves Hex either before a change (it happens on restart) or after
it (it's skipped). Nothing is applied twice, and nothing is missed: applying
the ledger is idempotent, whichever side restarts.

**Where to start.** On a brand-new ledger, `last_seq` starts at 0. If the
ledger already has events from testing, press **Refund all & clear table**
on the panel first (so no bets are riding), then start at `table.last_seq`.
Never skip events while bets are on the table: their payouts would be for
coins Hex never took.

### Step 1 — placing a bet

Handle one craps money action at a time: one lock around placing bets,
taking them down and the ledger tail. Then two quick `!bet`s can't both pass
the same balance check, and the tail can't meet a debit while Hex is still
waiting for the `/bet` reply that carries it.

Either of two orderings is safe.

**(a) Check the balance, bet, then debit.** Hex only takes coins for bets
that are on the table.

1. Check that `hexbank.balance(user)` covers the amount. Sync the ledger
   first, so winnings that just landed count.
2. `POST /games/api/craps/bet` with `{"user", "bet", "amount"}`.
3. For each `debits[]` entry `{user, amount, bet_id, seq, reason}`, call
   `hexbank.debit(user, amount)` and add `seq` to `done`, in one transaction.
4. **Rejected** bets have no debit and moved nothing, so reply with their
   `error`. A **409** `bets_closed` placed nothing because the dice are in the
   air, so try again after `retry_in_ms`. If Hex never gets a reply at all,
   it does nothing more: if the bet did reach the table, the tail takes its
   debit.
5. If a debit **fails** because the viewer spent the coins somewhere else in
   Hex between the check and the debit, take the bet back down with
   `POST /remove {"bet_id"}`. For an `odds` debit, send `{"user", "bet"}`
   with the same odds text instead, which takes down only the odds, even
   behind a contract bet. Hex never took those coins, so add the failed
   debit's `seq` and the refund's `seq` (in the `/remove` reply's `ledger`)
   to `done`, and neither one moves anything. The refund
   (`removed[].refund`) can be more than the failed debit, if it was an `add`
   or more odds and the whole bet or all its odds came down. In that case,
   `hexbank.credit` the difference, because that's the part Hex took earlier.

**(b) Debit, bet, then credit back.** The coins are taken the moment the
command arrives, so nothing else in Hex can spend them in the meantime.

1. `hexbank.debit(user, amount)`. If it fails, tell the viewer; nothing was
   sent. Save the `hold` (user, amount) together with the debit.
2. `POST /games/api/craps/bet`.
3. Add every `debits[]` `seq` to `done`, because those coins are already
   taken. Then `hexbank.credit` back the amount minus the `debits[]` amounts:
   the rejected part, or all of it on a 400 or a 409. Clear the `hold`. Do
   all of this in one transaction.
4. If there's **no reply** (a timeout, or Hexcast is down), credit it all back
   and clear the `hold`. If the bet did reach the table, its debit is in the
   ledger under a `seq` Hex never put in `done`, so the tail takes it. When Hex
   starts and finds a `hold` still saved, it does the same.

Odds (`!odds 200` → bet `odds`) and adding to a bet work the same way; their
debits have reason `odds` and `add`.

### Step 2 — paying out: tail the ledger

The ledger is the one place that has every coin movement, whoever caused it:
a roll from Hex, the panel's Roll button, [auto-roll](#auto-roll) or the
[roll timer](#roll-timer), a **⏹ Stop** that settles a roll in mid-air, a
take-down or a clear from the panel. Tail it:

1. `GET /games/api/craps/ledger?since=<last_seq>&limit=1000` (or
   `/games/api/ledger`, every game, when Hex runs roulette's table too).
2. For each event, oldest first:
   - If its `seq` is in `done`, Hex already handled it from a reply. Skip it
     and drop it from `done`.
   - For a `credit`, call `hexbank.credit(user, amount)`. The reasons are
     `win`, `win_stays`, `push`, `returned`, `remove` and `refund`.
   - For a `debit`, call `hexbank.debit(user, amount)`. This is a bet Hex
     didn't take the coins for itself: one placed with the panel's
     **Place a bet**, a curl, or a `/bet` whose reply never reached Hex. If
     the viewer can't cover it, that's Hex's call: let the balance go
     negative, or take the bet down.
   - Set `last_seq` to that event's `seq` and save it, together with the
     hexbank change.
3. Repeat until a call returns no events. Then poll every second or two, or
   connect to `ws://<host>:4747/games/ws/panel` and sync whenever a `ledger`
   message arrives. Also sync once on every connect, because pushes sent while
   Hex was disconnected are gone.

Losing bets have no event. Those coins were taken when the bet was placed,
and the house keeps them.

### Step 3 — announcing a roll

`POST /roll` with `{"user", "wait": true}` answers once the dice have landed
and the roll is settled. Use `result.call` / `result.sub` for the stickman
line, and `credits[]` (per user) and `settlements[]` (per bet) for who won
what. That reply is for the chat message only. The coins move in the ledger
tail, so sync right after it and balances are fresh when chat asks. A reply
sent **without** `wait` isn't due until the dice land, and a Hexcast restart
before then cancels the roll.

### Step 4 — take-downs and clears

`/remove` and `/clear` reply with `credits[]`, the coins going back (reasons
`remove` and `refund`). Use them for the chat reply. The coins themselves go
back through the ledger tail like every other credit. Take-downs and clears
made on the panel land in the ledger too, without Hex calling anything.

### Restarts

- **Hex restarts.** Nothing is lost. Hex picks up from the saved `last_seq`
  and `done` (and gives back a saved `hold`, for ordering (b)), and the tail
  replays everything it missed.
- **Hexcast restarts.** The table is restored from disk, so every bet is
  still there and still debited. `seq` numbering carries on where it stopped,
  so Hex's `last_seq` stays valid. `/ledger` reloads the latest 10 000 events
  from the file, so Hex can still catch up if it was behind. Roll history
  starts empty.
- **Hexcast restarts while the dice are in the air.** That roll is
  **cancelled**: nothing was committed, no credits were written, and the bets
  stay on the table exactly as they were. Hex's `wait=true` call fails with a
  connection error. Pay nothing, tell chat the roll didn't happen, and roll
  again once Hexcast is back.
- **Hex was down for a very long time.** `/ledger` serves the latest 10 000
  events. If `oldest_seq` is higher than `last_seq` + 1, the events in
  between aren't served any more. The full record is in
  `config/games_ledger.jsonl` (and `games_ledger.jsonl.1`), one JSON event per
  line, every game's. Apply the ones after `last_seq` from there — only the
  craps ones (`"game": "craps"`), unless Hex tails every game — skipping any
  in `done`, then carry on tailing.

### Rounding

Winnings are **rounded down to whole coins**; stakes always come back whole.
The amounts in the ledger are final, so pay exactly what they say and never
recompute them. To keep chat happy, suggest amounts that pay exactly:
multiples of 6 on place 6/8, multiples of 5 on place 4/5/9/10 and on odds on
6/8, and even odds on 5/9. The full list is under
[Amounts](#amounts-limits-and-rounding). Or pass the `hint` from `/validate`
to chat.

### Checking the books

Every coin is accounted for. For every user:

```
debits − credits = what they have on the table now (exposure) + what they've lost
```

and the house's result is `Σ debits − Σ credits − Σ exposure`. Coins are never
created or lost, except the documented round-down on winnings.

From Hex's side: once the tail has caught up, the total Hex has moved for a
viewer (everything it debited and credited for craps) equals their craps
ledger credits − debits. That holds in every case above, including a failed
debit that was taken back down.

### A chat-command flow

This one places bets with ordering (a).

| Chat | Hex |
| --- | --- |
| `!bet pass 100` | checks alice has 100 → `POST /bet {"user": "alice", "bet": "pass", "amount": 100}` → `hexbank.debit("alice", 100)` for the `debits[]` entry → `@alice Pass line 100` |
| `!bet place6 60` | same, bet `place6` |
| `!odds 200` | same, bet `odds` → takes 200 (reason `odds`). Over the limit, it's rejected with an `error` that says how much more fits |
| `!roll` | `POST /roll {"user": "alice", "wait": true}` → announces `result.call` / `result.sub` and the `credits` → syncs the ledger (pays) |
| `!mybets` | `GET /user/alice` → lists `bets` and `exposure` |
| `!down place6` | `POST /remove {"user": "alice", "bet": "place6"}` → syncs the ledger (the refund). `!down odds` takes down just the odds |
| `!craps` | replies with a short help built from `GET /bets` |

A whole hand, for example:

1. On the come-out, alice types `!bet pass 100`. Her balance is fine, the bet
   is accepted, and Hex debits 100 (seq 42). bob types `!bet field 25`, and
   Hex debits 25.
2. A mod types `!roll`: **2-4**, `POINT IS 6`. bob's field loses. There's no
   event, because his 25 was already taken. The pass bet stays, and the puck
   goes ON 6.
3. alice types `!odds 200`. That's within 5× her 100 flat on a 6, so it's
   accepted and Hex debits 200 (reason `odds`).
4. `!roll`: **3-3**, `WINNER 6`. alice wins 100 on the line and 240 on her
   odds (200 at 6:5). That's one credit of **640** (her 300 stake + 340 won),
   which Hex's ledger tail pays.

Who may `!roll` is up to you: the shooter only, mods, or anyone. If Hex runs
on a different machine from Hexcast, use Hexcast's LAN address (e.g.
`http://192.168.1.20:4747`) instead of `localhost`. A bot in the cloud can't
reach it, because Hexcast is LAN-only.

### Hex's side in code (Python sketch)

A sketch of both orderings. Hex needs only one of `bet_check_first` (a) or
`bet_debit_first` (b). `hexbank` and `state` are Hex's own. Any HTTP library
works (`pip install requests` for this one).

```python
import threading, time, requests

HEX = "http://localhost:4747/games/api/craps"   # Hexcast, as Hex's machine sees it
LOCK = threading.RLock()   # one craps money action at a time: bets, take-downs, the ledger tail

# Placeholders - the names are made up, use what Hex really has:
#   hexbank.balance(user) -> int
#   hexbank.debit(user, amount) -> bool     False when the viewer can't cover it
#   hexbank.credit(user, amount)
#   state.last_seq, state.done (a set), state.hold   Hex's record of the ledger (see above)
#   state.save()   commits that record TOGETHER with the hexbank changes made since the last save


def sync_ledger():
    """Apply every ledger event Hex hasn't handled yet, oldest first, exactly once."""
    with LOCK:
        while True:
            r = requests.get(f"{HEX}/ledger", params={"since": state.last_seq, "limit": 1000}, timeout=10)
            fresh = [ev for ev in r.json().get("events") or [] if ev["seq"] > state.last_seq]
            if not fresh:
                return                                       # caught up
            for ev in fresh:                                 # oldest first
                if ev["seq"] in state.done:
                    state.done.discard(ev["seq"])            # handled from a /bet or /remove reply
                elif ev["type"] == "credit":
                    hexbank.credit(ev["user"], ev["amount"])
                else:                                        # a bet Hex didn't take the coins for
                    hexbank.debit(ev["user"], ev["amount"])  # (panel, curl, a lost reply) - see Step 2
                state.last_seq = ev["seq"]
                state.save()


def bet_check_first(user, bet, amount):
    """(a) check the balance -> POST /bet -> debit each accepted debits[] entry"""
    with LOCK:
        sync_ledger()                                        # winnings that just landed count
        if hexbank.balance(user) < amount:
            return f"@{user} you have {hexbank.balance(user)} hexcoins"
        r = requests.post(f"{HEX}/bet", json={"user": user, "bet": bet, "amount": amount}, timeout=10)
        data = r.json()
        for d in data.get("debits") or []:
            if hexbank.debit(d["user"], d["amount"]):
                state.done.add(d["seq"])                     # the tail mustn't take it again
                state.save()
            else:                                            # spent elsewhere since the check
                take_back(d, bet)
                return f"@{user} you don't have {amount} hexcoins any more"
        return bet_reply(user, r.status_code, data)


def take_back(d, bet):
    """(a) a debit failed: take that bet (or those odds) back down - nothing moves in the hexbank"""
    body = {"user": d["user"], "bet": bet} if d["reason"] == "odds" else {"bet_id": d["bet_id"]}
    r = requests.post(f"{HEX}/remove", json=body, timeout=10)
    if r.status_code != 200:
        return                          # dice in the air, or already settled: the tail takes the debit
    rm = r.json()
    state.done.add(d["seq"])                                 # never taken...
    state.done.update(ev["seq"] for ev in rm["ledger"])      # ...so its refund isn't paid either
    extra = sum(b["refund"] for b in rm["removed"]) - d["amount"]
    if extra > 0:
        hexbank.credit(d["user"], extra)                     # the part of the bet Hex took before
    state.save()


def bet_debit_first(user, bet, amount):
    """(b) debit first -> POST /bet -> credit back whatever wasn't accepted"""
    with LOCK:
        if not hexbank.debit(user, amount):
            return f"@{user} you don't have {amount} hexcoins"
        state.hold = (user, amount)
        state.save()
        try:
            r = requests.post(f"{HEX}/bet", json={"user": user, "bet": bet, "amount": amount}, timeout=10)
            status, data = r.status_code, r.json()
        except (requests.RequestException, ValueError):
            status, data = None, {}                          # no reply: give it all back
        taken = 0
        for d in data.get("debits") or []:
            state.done.add(d["seq"])                         # already taken, up front
            taken += d["amount"]
        if amount > taken:
            hexbank.credit(user, amount - taken)             # rejected, 409, or no reply
        state.hold = None
        state.save()
        if status is None:
            return f"@{user} Hexcast didn't answer - your {amount} is back, try again"
        return bet_reply(user, status, data)


def bet_reply(user, status, data):
    if status == 409:
        return f"@{user} the dice are rolling - bet again in a moment"
    if data.get("accepted"):
        b = data["accepted"][0]
        return f"@{user} {b['label']} {b['amount']}" + (f" +{b['odds']} odds" if b["odds"] else "")
    rej = data.get("rejected") or [{}]
    return f"@{user} {rej[0].get('error') or data.get('error') or 'bet refused'}"


def on_roll(user):
    """!roll -> throw; answered when the dice land. The coins move in sync_ledger(), not here."""
    try:
        r = requests.post(f"{HEX}/roll", json={"user": user, "wait": True}, timeout=30)
        data = r.json()
    except (requests.RequestException, ValueError):
        return "The roll didn't go through - bets are still on the table, roll again"
    if r.status_code == 409:
        return f"The dice are busy - try again in {data['retry_in_ms'] // 1000 + 1}s"
    sync_ledger()                                            # pays everyone, exactly once
    res = data["result"]
    paid = ", ".join(f"@{c['user']} +{c['amount']}" for c in data["credits"]) or "no winners"
    return f"{res['call']} ({res['label']}) - {paid}"


def on_down(user, what):
    """!down place6 / !down odds / !down all - the refund comes back through the ledger"""
    body = {"user": user, "all": True} if what == "all" else {"user": user, "bet": what}
    with LOCK:
        r = requests.post(f"{HEX}/remove", json=body, timeout=10)
        data = r.json()
        sync_ledger()
    if r.status_code != 200:
        return f"@{user} {data.get('error')}"
    return f"@{user} {sum(c['amount'] for c in data['credits'])} hexcoins back"


def ledger_loop():
    """Catches everything the commands don't: auto-rolls, timer rolls, panel rolls, take-downs, clears."""
    while True:
        try:
            sync_ledger()
        except requests.RequestException:
            pass                                             # Hexcast restarting - try again
        time.sleep(2)


def start():
    """When Hex starts: give back a hold left by a crash (ordering b), then tail the ledger."""
    with LOCK:
        if state.hold:
            hexbank.credit(*state.hold)      # if that bet reached the table, the tail takes it again
            state.hold = None
            state.save()
    threading.Thread(target=ledger_loop, daemon=True).start()
```

The only places coins move are the bet functions (the debits Hex took for
its own bets, and their give-backs) and `sync_ledger` (everything else). Each
ledger `seq` is either marked `done` or applied by the tail, never both,
and `state.save()` makes that stick across crashes. That's the whole trick.

---

## Hex does the math (Mode B)

In Mode B Hexcast's table stays empty. Hex keeps the bets in its own code,
settles them from each roll's `result` and pays from its hexbank. Hexcast
throws the dice, moves the puck, and shows what Hex tells it to. See
[Two ways to run the money](#two-ways-to-run-the-money) for when to pick it.

### Step by step

1. **Take bets in Hex.** Check each bet against Hex's own rules, take the
   stake from the hexbank, and add the bet to Hex's list. When a rule depends
   on the phase — the pass line only on the come-out, place bets off on the
   come-out — read `phase` and `point` from `GET /games/api/craps/table`.
   Don't call `/bet`; that's Mode A.
2. **Mirror them.** After every change, `POST /games/api/craps/board` with
   Hex's bets. One line per viewer reads best, like the built-in board:
   `{"user": "alice", "text": "Pass 100 + odds 50", "amount": 150}`. With
   **show when bets** on, the tray stays up while the board has lines.
3. **Close bets, then roll.** Stop taking bets (the dice are decided the
   moment they're thrown), then `POST /games/api/craps/roll` with
   `{"user": <shooter>, "wait": true}` and **no `bets`**. On a **409** busy,
   reopen and try again after `retry_in_ms`.
4. **Settle from `result`.** The reply comes when the dice land.
   `result.event` says what the roll did — `natural`, `craps`, `point_set`,
   `point_made`, `seven_out` or `roll` — next to `total`, `dice`, `hard`,
   and the phase and point before and after it (`phase_before`,
   `point_before`, `phase_after`, `point_after`). Hexcast moves the puck on
   every roll in both modes, so settle against `phase_before` and
   `point_before` rather than a point Hex keeps itself; then Hex's bets and
   the puck on screen always agree. The standard rules are in
   [How bets resolve](#how-bets-resolve); in Mode B they can be Hex's own.
5. **Pay** every win and push from the hexbank.
6. **Show it, and reopen.** `POST /games/api/craps/announce` with `spin_id`
   set to the roll's `id` and one line per bet that had action — a positive
   `amount` for a win, negative for a loss, `0` for a push. The card replaces
   the payouts board for that roll. Then `POST /board` with what's still
   riding (`{"bets": []}` when nothing is), and take bets again.

**Only Hex rolls.** A roll from the panel's **Roll dice** or a curl still
moves the puck and the shooter, but Hex never sees its result, so Hex's
bets would miss that roll. (Auto-roll never starts in Mode B — it only
counts bets on Hexcast's table. For a countdown, Hex starts the
[roll timer](#roll-timer) itself and reads the roll it throws from
`/last`.)

**Restarts.**

- **Hex restarts.** Its bets and balances are its own: save the bet list
  together with the hexbank changes, like the rest of Hex's state, and post
  the board again when it's back.
- **Hexcast restarts.** Phase, point and shooter come back from disk, but the
  board is memory only — `table.display_board` is `null` again, so post it
  again. A restart while the dice are in the air cancels that roll: the
  `wait=true` call fails, so settle nothing and roll again.
- **⏹ Stop in mid-roll.** The reply comes back with `"landed": false,
  "stopped": true`, and the roll still counts: settle it.

### Hex's side in code (Mode B sketch)

A sketch with a few bets — pass line, don't pass, the field, place 6 and 8 —
settled by the standard rules. `hexbank.debit` and `hexbank.credit` are
placeholder names again. Any HTTP library works (`pip install requests` for
this one).

```python
import threading, requests

HEX = "http://localhost:4747/games/api/craps"   # Hexcast, as Hex's machine sees it
LOCK = threading.Lock()   # Hex's bets change in one place at a time
bets = []                 # Hex's own table: {"user": "alice", "kind": "pass", "amount": 100}
rolling = False           # no new bets while the dice are in the air
NAMES = {"pass": "Pass line", "dontpass": "Don't pass", "field": "Field",
         "place6": "Place 6", "place8": "Place 8"}

# Placeholders - use what Hex's hexbank really calls them:
#   hexbank.debit(user, amount) -> bool     False when the viewer can't cover it
#   hexbank.credit(user, amount)


def settle(b, res):
    """One of Hex's bets against one roll. None = no action (the bet stays), else
    (net, credit, stays): +winnings / -stake for the card, coins back now, still up."""
    kind, a, ev, total = b["kind"], b["amount"], res["event"], res["total"]
    if kind == "field":                                    # one roll: always comes down
        pays = {2: 2, 3: 1, 4: 1, 9: 1, 10: 1, 11: 1, 12: 3}.get(total)
        return (a * pays, a * (pays + 1), False) if pays else (-a, 0, False)
    if kind in ("place6", "place8"):
        if res["phase_before"] != "point":
            return None                                    # off on the come-out
        if total == int(kind[-1]):
            won = a * 7 // 6                               # 7:6, rounded down
            return won, won, True                          # winnings only - the bet stays up
        return (-a, 0, False) if total == 7 else None
    if kind == "dontpass" and ev == "craps":
        return (0, a, False) if total == 12 else (a, 2 * a, False)   # 12 pushes (bar 12)
    wins = {"pass": ("natural", "point_made"), "dontpass": ("seven_out",)}[kind]
    loses = {"pass": ("craps", "seven_out"), "dontpass": ("natural", "point_made")}[kind]
    if ev in wins:
        return a, 2 * a, False
    if ev in loses:
        return -a, 0, False
    return None                                            # point_set or another roll: no action


def show(path, body):
    """The display calls only change the screen - never let one break a bet or a payout."""
    try:
        requests.post(f"{HEX}/{path}", json=body, timeout=10)
    except requests.RequestException:
        pass


def post_board():
    """Mirror Hex's bets on the overlay: one line per viewer, like the built-in board."""
    per = {}
    for b in bets:
        per.setdefault(b["user"], []).append(b)
    show("board", {"bets": [{"user": u, "amount": sum(b["amount"] for b in mine),
                             "text": ", ".join(f"{NAMES[b['kind']]} {b['amount']}" for b in mine)}
                            for u, mine in per.items()]})


def on_bet(user, kind, amount):
    """!bet pass 100 - Hex's rules and Hex's hexbank; Hexcast only shows the board"""
    if kind not in NAMES or amount < 1:
        return f"@{user} bets: {', '.join(NAMES)}"
    with LOCK:
        if rolling:
            return f"@{user} the dice are rolling - bet again in a moment"
        if kind in ("pass", "dontpass"):
            table = requests.get(f"{HEX}/table", timeout=10).json()["table"]
            if table["phase"] != "come_out":
                return f"@{user} line bets go down on the come-out"
        if not hexbank.debit(user, amount):
            return f"@{user} you don't have {amount} hexcoins"
        bets.append({"user": user, "kind": kind, "amount": amount})
        post_board()
    return f"@{user} {NAMES[kind]} {amount}"


def on_roll(user):
    """!roll - Hexcast throws fair dice; Hex settles its own bets from the result"""
    global rolling
    with LOCK:
        if rolling:
            return "The dice are already rolling"
        rolling = True                                     # bets wait until this roll is settled
    try:
        r = requests.post(f"{HEX}/roll", json={"user": user, "wait": True}, timeout=30)  # no "bets"
        status, data = r.status_code, r.json()
    except (requests.RequestException, ValueError):
        status, data = None, {}                            # no reply: settle nothing, roll again
    with LOCK:
        rolling = False
        if not data.get("ok"):
            if status == 409:
                return f"The dice are busy - try again in {data['retry_in_ms'] // 1000 + 1}s"
            return "The roll didn't go through - bets are still on the table, roll again"
        lines = settle_roll(data)
    res = data["result"]
    paid = ", ".join(f"@{l['user']} +{l['amount']}" for l in lines if l["amount"] > 0) or "no winners"
    return f"{res['call']} ({res['label']}) - {paid}"


def settle_roll(data):
    """Pay Hex's bets from Hexcast's roll, then show it: the payouts card and the new board."""
    global bets
    lines, riding = [], []
    for b in bets:
        out = settle(b, data["result"])
        if out is None:
            riding.append(b)                               # no action this roll
            continue
        net, credit, stays = out
        if credit:
            hexbank.credit(b["user"], credit)
        lines.append({"user": b["user"], "amount": net, "text": NAMES[b["kind"]]})
        if stays:
            riding.append(b)
    bets = riding                                          # save it with the hexbank changes
    lines.sort(key=lambda l: -l["amount"])                 # winners first
    show("announce", {"spin_id": data["id"], "title": "PAYOUTS", "lines": lines,
                      "empty_text": "No action"})
    post_board()
    return lines
```

Call `post_board()` when Hex starts, and again whenever
`GET /games/api/craps/table` shows `display_board: null` while Hex has bets
down (Hexcast restarted).

---

## Auto-roll

With **auto-roll** on (`auto_roll: true`), the table rolls itself so chat can
play without a mod pressing Roll:

- When bets are on the table and the game is idle, a countdown of **bet
  window** seconds (`bet_window_seconds`, default 20) starts — on the first
  bet placed while idle, again after a roll's result (and cooldown) if bets
  remain, and when auto-roll is switched on with bets down.
- At zero the server rolls exactly like `/roll`, with the current shooter as
  the `user` — if auto-roll is still on and bets are still down.
- Any roll cancels it (a manual one, or a `test` roll from the editor's
  **Test in OBS**), and so do `/clear`, `/stop`, `/timer/cancel`, the table
  emptying and switching auto-roll off. After a test roll the countdown
  starts over from the full bet window, if bets are still down.
- `table.auto_roll_in_ms` is the time left (when the message was built),
  `null` when it isn't counting. The overlay shows it as **NEXT ROLL 0:12**
  and the panel shows it too.
- While it counts, the tray stays on screen — even with **show when bets**
  off — unless `/hide` or **⏹ Stop** took it off.
- A countdown doesn't survive a Hexcast restart. The bets do; auto-roll
  counts again from the next bet placed (or start the
  [roll timer](#roll-timer)).

Rolls a countdown throws pay out like any other roll — through the ledger. A
bank that only reads `/roll` responses would miss them; tail the ledger.

Auto-roll only counts bets on Hexcast's own table, not a board Hex posted
with `/board` — so in [Mode B](#hex-does-the-math-mode-b) it never starts,
and Hex decides when to roll (the [roll timer](#roll-timer) can count down
to it).

---

## Roll timer

`GET` or `POST /games/api/craps/timer` starts a countdown now — with or
without bets on the table, auto-roll on or off. At zero the dice go exactly
like an auto-roll, with the current shooter as the `user`, even with nothing
on the table. Called while a countdown runs, it starts over. The Craps tab's
**Start timer** does the same with the bet window, and **Cancel timer** stops
it.

```
curl "http://localhost:4747/games/api/craps/timer?seconds=30"
curl http://localhost:4747/games/api/craps/timer/cancel
```

- `seconds` goes in the query string or the JSON body: 5–300, clamped; left
  out or not a number, it's `bet_window_seconds`. The reply is
  `{"ok": true, "auto_in_ms": 30000, "table": TABLE, "state": STATE}`, and
  `table.auto_roll_in_ms` counts it down from there — the overlay's
  **NEXT ROLL** timer, like auto-roll's.
- While a roll is in the air, showing its result or in cooldown it's **409**
  busy, the same body as a busy [`/roll`](#roll). Nothing started; try again
  after `retry_in_ms`.
- What stops it: any roll (a `test` roll too — the timer is simply gone
  then, start it again), `/stop`, `/timer/cancel` and `/clear`. Unlike
  auto-roll's countdown, taking the last bet down or switching auto-roll off
  doesn't.
- `GET|POST /games/api/craps/timer/cancel` stops any countdown, the timer's
  or auto-roll's, and nothing is thrown:
  `{"ok": true, "cancelled": true, "table": TABLE, "state": STATE}`
  (`cancelled` is `false` when none was running). With auto-roll on, the
  next bet placed starts a fresh auto-roll countdown.
- While it counts, the tray stays on screen, even with **show when bets**
  off; `/timer` also brings back a tray that `/hide` took off.
- In [Mode B](#hex-does-the-math-mode-b), where auto-roll never starts, it's
  how Hex gets a countdown: `/timer`, then `/board` as Hex takes bets, and
  Hex closes its bets when the countdown runs out. Nobody's call waits for
  the roll the timer throws, so Hex watches `GET /games/api/craps/last` for
  a new roll `id` and settles from that roll's `result`.

Roulette has the same call, with a **NEXT SPIN** countdown — see
[Games → Timer](games.md#timer).

---

## Storage

| File (in `config/`, or `HEXCAST_CONFIG_DIR`) | What |
| --- | --- |
| `games.json` | settings (the `"craps"` section) |
| `games_craps_table.json` | the table: phase, point, shooter, hand rolls, every bet — saved after every change |
| `games_ledger.jsonl` | the ledger, shared with roulette's table: every game's events, one JSON event per line (`.1` is the previous 10 MB) |

Roll history is in memory only (up to 200 rolls, reset on restart), and so
are Hex's [board and payouts card](#announce-and-board) (Mode B). Don't
delete `games_craps_table.json` while bets are on it — those coins were
debited; use **Refund all & clear table** so the refunds go through the
ledger. Same security posture as the rest of Hexcast: no auth, keep it on the
LAN.

---

## Troubleshooting

**`/games/api/craps/...` gives a 404 `unknown game`, there's no Craps tab,
or the tab says "Craps isn't running on this Hexcast yet".** The module loads
at startup — restart Hexcast after updating, and reload the panel.

**The tray never shows up in OBS.** Hover the Games dot in the top bar: "no
overlay connected" means the browser source isn't connected. Check the URL is
`/games/overlay` (and any `?game=` is `craps`), **Shutdown source when not
visible** is off, and the source is in the live scene. Right-click →
**Interact** → **Ctrl+Shift+R** reloads it.

**The tray stays on screen between rolls.** That's **show when bets** — it
stays up while bets are on the table, or while Hex's [board](#announce-and-board)
has lines (Mode B: post `{"bets": []}` when nothing is riding). Turn it off in
Settings, or `/hide` it. An announce card also keeps the tray up until its
`seconds` run out, and a running countdown ([auto-roll](#auto-roll) or the
[roll timer](#roll-timer)) until it rolls.

**`/timer` returns 409 `busy`.** The last roll is still in the air, showing
its result or in cooldown — start the countdown after `retry_in_ms`.

**A bet is rejected.** Read its `error`. The usual reasons: pass / don't pass
only on the come-out, come / don't come only with a point on; odds need your
own line or come bet (and a point, or a travelled come bet) and stay within
the [odds limit](#amounts-limits-and-rounding); amounts must be whole and at
least `min_bet`; a flat bet can't go over `max_bet`; horn amounts must be a
multiple of 4 and C&E even; a pass or come bet can't be raised once it's a
contract bet.

**409 `bets_closed`.** The dice are in the air. Bets reopen the moment they
land — retry after `retry_in_ms`.

**409 `busy` on a roll.** The last roll is still going — in the air, showing
its result, or in cooldown. **⏹ Stop** ends it (the roll still counts).

**Can't take a bet down.** Pass bets after the point is set and come bets
that have travelled are contract bets; they stay until they win or lose.

**A payout is a coin short.** Winnings round down to whole coins — place 6
with 10 pays 11, not 11.67. Use the amounts that pay exactly.

**Place bets didn't pay (or lose) on a come-out roll.** They're off on the
come-out; they come back on when a point is set.

**Come odds came back on a come-out 7.** Come odds are off on the come-out:
the flat bet loses, the odds are returned (`returned`).

**Hex's balances are out of step.** Sync from Hex's saved `last_seq` until
`/ledger?since=<last_seq>` comes back empty (`table.last_seq` can be higher
than the last craps seq: roulette's events share the numbering). Pay only
from ledger events,
apply each `seq` once (or mark it `done`, never both), and never pay from a
`/roll` reply: it's for announcing. For one viewer, what Hex has moved for
craps should equal their ledger credits − debits (see
[Checking the books](#checking-the-books)). The ledger file in `config/` has
the full record.

**`hexcast.log` says "games_craps_table.json is older than the ledger (table
at seq X, ledger at Y)".** A save of the table failed (disk full, or the file
locked by antivirus / a sync tool) and Hexcast stopped before the next save.
The bets moved by the craps seqs the line names (after X; roulette's events
don't count) may be missing from — or still on — the table.
Before the next roll, compare the **On the table** card with those ledger
events, and take down or re-place bets by hand to match.

**`/announce` returns 409 "stale".** The `spin_id` you sent isn't the current
roll (or, with none in progress, the last committed one) — another roll has
started since (the reply's `spin_id` is the one it expected). Skip the card
for the old roll; don't re-post it without `spin_id`, or it would land on the
new roll.

**The bets board shows the wrong bets (Mode B).** While Hex has a board up
(`table.display_board` isn't `null`), the bets board shows it and nothing
else — post Hex's current bets again, or `/board/clear` to go back to
Hexcast's table. After a Hexcast restart the board is gone (`null`): Hex has
to post it again.

**The payouts card doesn't show, or goes too soon (Mode B).** Posted while the
dice are in the air, it waits for the landing. By default it stays up for the
rest of the result phase (or **result seconds** once that's over) — send
`seconds` (up to 120) for longer. A line with neither a `user` nor a `text`,
or with an `amount` that isn't a number, is skipped, and with **show
payouts** off no card is shown at all. The next roll and `/announce/clear`
take it down; `/hide` and **⏹ Stop** hide the tray with it, but the card runs
on until its `seconds` are up. `announce` in `GET /games/api/status` is
`null` when there's no card.

**No dice sounds.** Turn on **Control audio via OBS** on the browser source;
check **Sound** in the Edit Mode editor (`sfx` / `sfx_volume`). In a normal browser tab, sound is blocked until
you click the page once. The panel's preview is always silent.

**The roll/landing clip doesn't play.** It plays on the soundboard overlay
(`/overlay`), which must be in the scene; the name must match a clip in your
library; test rolls never fire cues.
