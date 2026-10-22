# Hellhound Derby — hellhounds, one track, and chat's coins

Six hellhounds race on one track in hell, while a crowd of demons throws junk at them.
The racers are the hounds Cinder, Gnash, Three-Heads Terry, Sparks, Beelzebubba,
Ember, Lil' Brimstone, Mange and Hungry Harry (a skeleton). They trip, bite each
other, slip in slime, get their arses set on fire by lava geysers, stop for a
snack, and talk trash every time one passes another. Chat bets against the
**bank** (your bot and its coin bank) on **the winner**. Every hound wears a race
**number**, so a bet is a **number** and an **amount**. If your hound wins, you
are paid the **multiplier** on its card. Any other result loses the stake.

The odds aren't fresh dice every race: they come from the **form book**, a
rolling record of every hound's results. A hound on a winning streak is the
favourite and pays less, and a hound that hasn't won in ages pays more. Streaks
show up in the trash talk too ("I won last time, bitch, and I'll do it again!").

The **server** decides the result (fairly, below); the overlay only acts it out.
Everything lives under `/games/api/derby/*`, and the game has its own tab on the
Games panel: `/games#derby`. It is an add-on of the [Games](games.md) plugin:
install it from the **+** in the Games tab. The header's name is yours (`title`,
default **Hellhound Derby**): see [Edit Mode & branding](#edit-mode--branding).

---

## At a glance

| | |
| --- | --- |
| A game | one race: the **lineup** (bets open, default 22 s) → the **race** (about 45 s) → the **result** card (12 s) |
| Field | 3–6 hounds (setting `field_size`, default **6**) drawn from the roster of 9, wearing the numbers 1–6 |
| Bets | `{amount, number}`: back as many hounds as you like; the same hound again adds to it |
| Payout | amount × the hound's multiplier, rounded **down** to whole coins; a lost stake is gone |
| Multiplier | `(1 − house edge) / chance`, floored to 2 decimals, never below ×1.01; on the lineup and at `GET /games/api/derby/bets` |
| Odds | from the form book (recent wins, streaks) plus a little luck on the day, fixed when the lineup opens |
| House edge | 5% (`house_edge_pct`, 0–25) |
| Photo finish | when the runner-up (or the last two) cross within about a head: the race freezes on a print ("CINDER WINS BY A WHISKER!") |

---

## Fairness

The outcome is never in the show's hands:

1. **When the lineup opens**, every hound's chance is fixed from its form (below)
   and shown with its multiplier. Nothing changes them during the window.
2. **When the bets close**, the winner is drawn with exactly those chances
   (Python's `secrets.SystemRandom`, a cryptographic RNG). The rest of the order
   is drawn the same way from the hounds left, but only the winner pays.
3. **Only then is the race written.** Each hound's finishing time, speed wobble,
   tumbles, bites, slime, geysers, the crowd's junk and every line of trash talk
   come from `random.Random(seed)` with a fresh seed. Every hound's run is then
   stretched so it crosses the line at its own finishing time. A hound that eats
   dirt in the first straight was always going to finish where it finishes; the
   chaos is dressing.
4. The script reaches the overlay when the race starts, after the bets have closed.

The overlay replays the server's maths to the last digit, which a test checks:
every hound crosses the line on screen in the drawn order. A refreshed page, a
second OBS and the panel's mirror all show the same race.

---

## The form book

`config/games_derby_form.json` keeps every hound's record: races, wins, its last
10 places (newest first), and its streak (+n = won the last n, −n = n races
without a win). Every real race adds to it; test races don't. The panel's
**Form book** card shows it, and **Clear the form book** resets every hound to
no races (equal odds again). `GET /games/api/derby/form` reads it, and
`POST /games/api/derby/form/clear` resets it.

A hound's **strength** going into a race:

```
rate     = (wins in its last 10 + 1) / (races in its last 10 + 6) × 6    (1 = an average hound; few races ≈ 1)
lift     = streak ≥ 1: +0.15 per win in a row (max +0.6)  ·  streak ≤ −1: −0.03 per race without a win (max −0.3)
strength = max(0.05, rate^(0.8 × form_weight) × (1 + lift × form_weight)) × e^(uniform(−luck, +luck))
chance   = strength / the sum of the field's strengths
```

`form_weight` (0–2, default 1) sets how much form counts: at 0 every hound is the
same except for luck. `luck` (0–1, default 0.35) is the day's spread. The lineup
and the overlay show a note per hound: *first race*, *won last time*, *won the
last 3*, *2 wins in 7*, *5 races without a win*.

---

## The bets (all against the bank)

- A bet names a hound by its **number** (`number`; `num`, `hound`, `pick`, `dog`,
  `runner` and `bet` work too). `3`, `"#3"`, `"no. 3"` are all fine, and so is the
  hound's name (`"cinder"`, `"Three-Heads Terry"`, `"Terry"`).
- A player may back several hounds. Betting a hound again adds to that bet.
- If the hound wins, the bet pays `floor(amount × multiplier)`, capped by
  `max_payout` but never below the stake. Every other bet is lost.
- `/remove` takes back what went down in this window (all of a player's bets,
  or one hound's) as a refund. It only works while the bets are open.

### Limits

`min_bet` (default 1) per bet; `max_bet` (default 100000, 0 = none) is all of a
player's bets on one race together; `max_payout` (0 = no cap) per winning bet.
At most 500 players per race.

---

## Timeline of a game

| Phase | How long | What the overlay shows |
| --- | --- | --- |
| `betting` | `bet_seconds` (22; `/start {seconds}` overrides) | the lineup: the odds board (number, name, form note, multiplier, the money on it), a spotlight on each hound in turn with its bio, odds and a bit of trash talk, the backers board, "BETS CLOSE IN n" and your bet command |
| `racing` | the script's `duration_ms` | the gate countdown (3.2 s), the race (the winner takes about `race_seconds`, the rest follow), the photo finish when there is one (4.6 s), and a 2 s run-out |
| `result` | `result_seconds` (12) | the finishing order with the winner's multiplier, *DEAD LAST*, the photo margin, who won and lost what |

Then the game ends (idle). If nobody bet, the race still runs as a show
(`run_without_bets`, default on); turn that off and a lineup without bets ends
quietly. `/next` ends the current phase early.

---

## The race and the show

**The hounds.** Each one has its own run (gait, head carriage, stride rate), its
own effect, and its own antic:

| Hound | Effect | Its antic |
| --- | --- | --- |
| Cinder | burning paw prints, embers | flares at a rival |
| Gnash | a slime trail | snaps at the one ahead |
| Three-Heads Terry | (three heads) | the heads argue with each other |
| Sparks | lightning footprints | zaps a rival |
| Beelzebubba | drool | stops to eat a soul, or the crowd's junk |
| Ember | a tail on fire | shows off |
| Lil' Brimstone | sulfur | yaps at everyone |
| Mange | flies and dust, a limp | stops to scratch fleas |
| Hungry Harry | soul fire in the eyes | runs back for a femur |

**Everyone** can trip, slip in slime, get grabbed by a demon hand from the
ground, leap a lava geyser (or not: the flaming arse), bite or kick a neighbour,
and get hit by junk from the stands (a bone, a skull, a flaming trash bag, a mug
of blood, a toilet...). The big ones (a tumble, the hand, a snack, Harry's femur)
cost a lot of ground; slime, a bite, a zap or Terry's arguing cost some; a bonk,
a kick or yapping hardly any; a flaming arse ends up as a booster. The crowd also
throws plenty that misses and lies around on the track (`show_crowd` hides those
lobs; junk that hits a hound is part of the race and always shows).

**The crowd.** Three tiers of monsters fill the stands behind the track. They
cheer, pump their fists, jeer and clap, calmer during the lineup and wilder as
the finish nears. Every piece of junk is thrown by someone in the front row: you
see the throw, and the junk leaves that monster's hand.

**Trash talk.** The hounds only talk when one passes another (with a cooldown),
when something happens to them, and at the finish. It's hard language by
default; streaks feed it ("2 in a row, motherfuckers!"). The **clean language**
setting turns every swear into `#@$%&`. `show_talk` off hides the bubbles. The
commentary line at the bottom names every event.

**Photo finish.** When the runner-up is within about a head of the line as the
winner crosses, the race freezes on a sepia print of that instant. The print
shows the line in red, the two hounds' numbers, and the margin: *A WHISKER*,
*A NOSE*, *A TONGUE*, *A HEAD*. A close fight for dead last gets one too.

**Sound.** The overlay makes its own sounds (no files): the countdown, the gate
horn, the crowd, thuds, bonks, splats, zaps, growls on a pass, the camera
shutter, a fanfare for the winner and a sad trombone for dead last. `sfx` /
`sfx_volume`.

---

## Test games

`/start` with `"test": true` (the panel's **Test race**) plays exactly the same
but **writes nothing to the ledger and nothing to the form book**: the lineup says
`TEST GAME`, and test races don't count in the stats. Use them to rehearse.

---

## HTTP API

Every call returns `{"ok": true|false, ...}`; GET query parameters and a POST
JSON body both work (the body wins). Actions return the game's fresh `state`.

| Call | What it does |
| --- | --- |
| `GET\|POST /games/api/derby/start` | `{seconds (the bet window, 5–300), test}` → `{started, field, state}`; **409** while a game runs |
| `GET\|POST /games/api/derby/bet` | `{user, amount, number}` → `{num, hound, name, side: "#3 Cinder", amount, total, mult, pays, debits:[{user, amount, seq, reason}], player}`; the same hound again adds; **409** `bets_closed` outside the lineup; **400** for a bad hound or amount |
| `GET\|POST /games/api/derby/remove` | `{user, number?}` — take back what went down in **this** window → `{credits, ledger}` (refunds) |
| `GET\|POST /games/api/derby/next` | end the current phase now → `{skipped}` |
| `GET /games/api/derby/table` | the STATE (below) |
| `GET /games/api/derby/user/{name}` | `{player, session}` — the player's bets now + session debits / credits / net |
| `GET /games/api/derby/ledger?since=0&limit=500` | this game's ledger events |
| `GET /games/api/derby/bets` | the rules, the odds model, the current field (with chances and multipliers) and the form book |
| `GET /games/api/derby/validate?number=3&amount=100` | dry-run a bet: hound, chance, multiplier, payout |
| `GET /games/api/derby/form` · `POST /games/api/derby/form/clear` | the form book · reset it |
| `GET\|POST /games/api/derby/stop` | end the game now: a race already drawn counts (winners are paid), an open window is refunded |
| `GET /games/api/derby/history · last` | finished races + stats (races, photo finishes, favourite / longshot wins, wins per hound) · the last one |
| `GET\|POST /games/api/derby/show · hide` | keep the idle scene (the form book) on screen · hide it |
| `GET\|POST /games/api/derby/preview` | `{overrides: {appearance keys}, seconds (2–60, default 8)}` — the Edit Mode editor's **Test in OBS**: the scene on screen for `seconds` with that look (`STATE.preview`). Never touches the game; `/preview/clear` ends it |
| `GET · POST /games/api/config` | the `"derby"` section (every key in [Settings](#settings)) |

`/spin`, `/play`, `/timer` and `/announce` answer 400 for this game: a game starts
with `/start`.

### STATE

`STATE.game` is null between games (`STATE.idle` then has the form book).

```jsonc
{
  "id": "db-1a2b3c4d", "test": false,
  "phase": "betting",               // betting | racing | result
  "ends_in_ms": 12400, "phase_ms": 22000, "elapsed_ms": 9600,
  "field": [{"num": 1, "id": "cinder", "name": "Cinder", "short": "Cinder",
             "trait": "Paws of fire. Thinks it's the fastest thing in Hell.",
             "row": 2, "chance": 0.231, "mult": 4.11, "note": "won the last 2", "streak": 2}, "..."],
  "edge_pct": 5,
  "markers": [{"num": 1, "total": 300, "users": [{"user": "alice", "amount": 200}, "..."]}, "..."],  // money on each hound
  "players": [{"user": "alice", "stake": 250, "bets": [{"num": 1, "hound": "cinder", "amount": 200, "mult": 4.11, "pays": 822}, "..."]}],
  "at_risk": 450,
  "script": null,                   // the race (racing / result): below
  "last": {"order": [3, 1, 6, 2, 5, 4],                      // race numbers, the winner first
           "winner": {"num": 3, "id": "terry", "name": "Three-Heads Terry", "mult": 5.46},
           "last": 4, "photo": {"kind": "win", "label": "A NOSE"},
           "winners": [{"user": "bob", "num": 3, "amount": 50, "mult": 5.46, "pays": 273}],
           "losers": [{"user": "alice", "num": 1, "amount": 200}]},
  "outcome": null, "summary": null, // set when the race ends
  "currency": "coins", "min_bet": 1, "max_bet": 100000, "commands_text": "!derby <number> <coins>"
}
```

`script` is everything the overlay needs to play the race: `seed`, `length`
(world px; 100 px = 1 m), `v`, `dt`, `gate_ms`, `photo_ms`, `duration_ms`,
`runners` (`[{i, num, id, T (its finishing time, s after the gate), wob (its
speed wobble)}]`), `order` (runner indexes, the winner first), `events`
(`[{type, t, dur, a, b, me, them, hazard / thrown, obj, at, line / lines, by,
ticker}]`: what happens, to whom, its speed curve, what is said), `photo`
(`{kind: win|last, a, b, at, label}` or null) and `talk` (the finish lines).

### Ledger reasons

| type | reason | when |
| --- | --- | --- |
| debit | `bet` / `add` | a bet / more on the same hound this window |
| credit | `win` | a bet on the winner, when the race ends: amount × multiplier, rounded down |
| credit | `refund` | a bet taken back (`/remove`), or an open window's bets returned by `/stop` |

Each event's `bet_id` is `<game id>/n<number>` and its `label` is `#3 Three-Heads
Terry` (a win adds the multiplier: `· won x5.46`). The ledger is the same one
every game uses: one `seq` numbering, and each event names its `game`. See
[The shared ledger](games.md#the-shared-ledger).

---

## Your bot's side

Hexcast never reads chat. Your bot turns chat commands into API calls and pays
from the ledger. A suggested command set (put yours in the **Commands line**
setting so the lineup shows it):

| Chat | Call |
| --- | --- |
| `!derby` (mod) | `POST /start` |
| `!derby 3 100` | `POST /bet {user, number: 3, amount: 100}` |
| `!takeback` | `POST /remove {user}` |
| `!odds` | `GET /bets`: the field with every hound's chance and multiplier |
| `!form` | `GET /form` |

Before a `/bet`, check the player can afford it. The debit is Hexcast's word
that the stake was taken: debit it in your bot's bank when you see it in the
ledger, or right away from the `/bet` reply's `debits`, but never both. The
`/bet` reply already says the multiplier and what the bet would pay.

---

## Settings

| Key | Default | |
| --- | --- | --- |
| `field_size` | 6 | hounds per race (3–6) |
| `benched` | "" | hounds that never race, comma separated (ids or names: `cinder, gnash, terry, sparks, bubba, ember, brimstone, mange, harry`); at least 3 must be left |
| `bet_seconds` | 22 | the lineup / betting window (5–300) |
| `race_seconds` | 45 | roughly how long the winner takes (25–90) |
| `result_seconds` | 12 | the result card (3–60) |
| `run_without_bets` | on | race even when nobody bet |
| `house_edge_pct` | 5 | 0–25 |
| `form_weight` · `luck` | 1 · 0.35 | how much form moves the odds (0–2) · the day's random spread (0–1) |
| `min_bet` · `max_bet` · `max_payout` | 1 · 100000 · 0 | per bet · per player per race (0 = none) · per winning bet (0 = no cap) |
| `currency` | coins | the name after amounts, ≤ 24 characters |
| `commands_text` | `!derby <number> <coins>` | your bot's bet command, shown on the lineup |
| `start_clip` · `finish_clip` | "" | soundboard clips (play on the base `/overlay`): the gates open · the race is over |
| `title` | "Hellhound Derby" | the header's name: your branding (≤ 32 characters) ✓ |
| `x` · `y` · `scale` · `theme` | 50 · 50 · 1 · inferno | placement (0–100 % · 0–100 % · 0.2–5 × the 1920×1080 scene) · `inferno`, `abyss`, `sulfur` ✓ |
| `show_rules` · `show_players` · `players_max` | on · on · 8 | the how-to-bet line · the backers board and the result card's players · how many ✓ |
| `show_talk` · `clean_language` · `show_crowd` | on · off · on | speech bubbles · swears become `#@$%&` · the crowd's lobs ✓ |
| `sfx` · `sfx_volume` | on · 0.6 | the overlay's own sounds ✓ |
| `hide_when_idle` | on | off = the form book stays on screen between games |

✓ = an appearance key: what the Edit Mode editor edits, and what a Test in OBS
(`/preview`) may override. A change to the odds (`house_edge_pct`, `form_weight`,
`luck`, `field_size`) applies from the **next** lineup: the odds on screen never
change under a bet.

### Edit Mode & branding

Turn on **Edit Mode** (the hexbar) and click the Hellhound Derby card (or its **✎
Edit placement** button): the same placement & look editor as the other games.
It shows the real scene on a 16:9 preview of the 1920×1080 stage, fed a sample
lineup (six hounds, six sample backers). Drag it (or use the 3×3 quick grid),
set **Scale** 0.2–5, **Theme** (Inferno's lava, Abyss's cold blue fire, Sulfur's
yellow brimstone), **Title**, the rules line, trash talk, clean language, the
crowd, the players board, and sound and volume. All of it is live in the preview. **▶ Preview** plays a short local demo race (no
coins); **Test in OBS** puts the unsaved look on the real overlay for 8 seconds;
**Save** stores it; **Reset** goes back to the defaults.

---

## Storage & restarts

`config/games_derby.json` holds the running game (and the last finished ones +
stats); `config/games_derby_form.json` is the form book (clearing the history
keeps it). Every change is written before its ledger events, so a crash can't pay
twice or lose a stake. A game still running when Hexcast stops is **settled at the
next start-up**: a race whose result was already drawn counts and the winners are
paid; the bets of an open window are refunded.

---

## Art & credits

The hounds are pre-rendered sprite sheets (`static/hounds/<id>.webp`, 16 frames of
a gallop each) with an anchor file (paws, tail, mouth, eyes) for the effects.
Nobody who installs the add-on needs Blender. The sources and the render
pipeline are in `art/derby/` (see its README); the third-party model files are
never committed.

Both models are **CC BY 4.0**:

- "Hell Hound" by Adam (shadow798), https://sketchfab.com/3d-models/hell-hound-36f3f384fd9b41a9a8dae1aaf22349d3
  (posed, recoloured, rendered; Terry's extra heads are copies of its head).
- "Dog (Canis familiaris) Dolichcephalic" by The University of Edinburgh (CT courtesy of
  Dr. Tobias Schwarz, prepared by Brian Mather), https://sketchfab.com/3d-models/5d8d86a270b64f05aab45c3d09f350a6
  (Hungry Harry's skull).

The crowd is twelve **Mixamo** characters (Adobe, https://www.mixamo.com), each
a sprite sheet (`static/crowd/<id>.webp`: idle, cheer, fist pump, jeer, clap and
the throw, 8 frames each; `derby_crowd.json` says where the feet and the throwing
hand are). Adobe lets Mixamo characters be used royalty free in commercial
projects as long as the character files themselves aren't handed out, so only
the rendered frames ship. The moves were posed for Hexcast, without Mixamo's
animations.

The credits ship with the add-on in `static/CREDITS.md` and its help page.
