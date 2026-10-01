# Blackjack — Hex deals a Vegas shoe game to chat's coins

A half-moon felt table with **Hex** behind it as the dealer, a shoe on one side
and a discard tray on the other. Chat sits down at up to **14 seats** (the rest
wait in a queue), bets against the **bank** (your bot and its coin bank), and
plays standard Vegas blackjack from chat commands: hit, stand, double, split,
insurance, surrender. Everybody plays **at the same time**: each action round
is a short window in which every hand sends one choice, then Hex deals them
out in seat order.

The **server** shuffles the shoe, deals every card and decides every hand; the
overlay only animates it. Everything lives under `/games/api/blackjack/*`, and
the game has its own tab on the Games panel: `/games#blackjack`. (It is an
add-on of the [Games](games.md) plugin: install it from the **+** in the Games
tab.) The plaque on the rail is yours (`title`, default **Blackjack**) — see
[Edit Mode & branding](#edit-mode--branding).

---

## At a glance

| | |
| --- | --- |
| Seats | 10 (setting `seats`, 1–14) in an arc; a waiting list of 20 (`queue_max`) |
| Shoe | 2 · 4 · 6 · 8 decks by how many are seated (1–2 → 2, 3–4 → 4, 5–7 → 6, 8+ → 8; setting `deck_table`), cut card at 75 % |
| Rules (defaults) | dealer stands on all 17s · dealer peeks · blackjack pays 3:2 · double on any two cards · double after split · split to 4 hands · split aces one card, no resplit · insurance 2:1 · no surrender · no Charlie |
| House edge | **0.26 % (2 decks) … 0.45 % (8 decks)** with the defaults and basic strategy — computed from the rules, shown in the panel |
| Bets | an ante per hand (`min_bet` 1 – `max_bet` 100000); doubles, splits and insurance are extra, taken at once |
| Timings | 30 s first bet window · 15 s "next hand" window · the deal (0.24 s a card: about 3 s for 3 players, 8 s for 14; + 3.6 s when a new shoe is shuffled) · 8 s insurance (only against an ace) · 12 s first action round, 8 s later ones (up to 8) · the dealer 1.5–4 s · 7 s payout screen · 8 s table-closed card |
| No bets | 3 betting windows in a row with nobody betting close the table (`idle_windows`) |
| Typical hand | about a minute |

---

## Fairness

- The shoe is built from 2–8 decks shuffled with `secrets.SystemRandom()` (the
  operating system's cryptographic random source). It lives in the server's
  memory only. Nothing — the API, the panel, a bet, the timing of a bet — can
  pick or nudge a card.
- **What is on screen is all there is.** The STATE the overlay is given never
  contains a card the table hasn't turned over: the dealer's hole card is
  `null` until Hex turns it (or peeks: then the STATE only says "no
  blackjack"), and the shoe is reported as counts (decks, cards left, cards to
  the cut, discards), never as an order.
- The shoe is reshuffled before the next hand once the **cut card** (75 % of
  the shoe by default) has been dealt past. A shoe that the table has outgrown
  (more players than it was built for) is replaced by a bigger one at the next
  shuffle, or at once when it can't cover a hand; if a shoe ever runs dry in
  the middle of a hand it is rebuilt from every card that is not on the felt.
- The house edge you see is **computed**, not typed in: an exact infinite-deck
  enumeration of basic strategy for the table's rules (dealer S17/H17, peek,
  blackjack payout, doubling limits, DAS, splits, resplit aces, surrender,
  Charlie), plus the usual finite-shoe correction of about 0.5 % / decks.
  Insurance is a side bet (about 7.7 % for the house) and isn't in the figure.

| Rule change (6 decks) | Change in the house edge |
| --- | --- |
| Defaults | 0.43 % (the starting point) |
| Dealer hits soft 17 | +0.22 % |
| Blackjack pays 6:5 | +1.35 % |
| No double after split | +0.14 % |
| Double on 10–11 only | +0.18 % |
| Split to 2 hands (`max_hands` 2) | +0.06 % |
| Resplit aces | −0.08 % |
| Late surrender | −0.09 % |
| Five-card Charlie wins | −1.58 % |

`GET /games/api/blackjack/bets` returns the live figures (they follow the
settings, including the edge per shoe size).

---

## How a hand plays

```
POST /start ─► betting (30 s: open_bet_seconds)   take a seat with a bet · "Place your bets!"
                 │  3 empty windows in a row → the table closes ("no bets")
                 ▼
               dealing (0.24 s a card)   shuffle if a new shoe is needed · two cards each, Hex's second card face down
                 │
                 ├─ Hex shows an ace ─► insurance (8 s: insurance_seconds) · half the ante, pays 2:1
                 ▼
               peek                 Hex looks at the hole card (when the rules say so): blackjack ends the hand
                 ▼
               action round 1 (12 s: action_seconds)   every live hand sends ONE choice ─┐
                 ▼                                                                       │ everybody answered:
               resolve (1–4 s)      the round's cards are dealt out, in seat order  ◄────┘ the window closes early
                 │  live hands again → action round 2, 3 … (8 s: later_action_seconds, up to max_rounds)
                 ▼
               dealer (1.5–4 s)     Hex turns the hole card and draws (S17 / H17)
                 ▼
               settle (7 s: settle_seconds)   verdicts and payouts, chips move
                 ▼
               betting (15 s: bet_seconds)    "next hand": bet again to keep your seat
                 …  and so on, until the table closes
               over (8 s: summary_seconds)    the table-closed card: hands, wagered, paid, house net
                 ▼
               idle
```

`POST /games/api/blackjack/next` (or the panel's **Skip ▶**) ends the current
window right away — handy for testing.

### Seats and the waiting list

- The first bet takes a seat: the first free one in the seating order (setting
  `seat_fill`: **center** hands them out from the middle outwards, so a small
  table sits in the middle of the felt; **first** goes 1, 2, 3 …), or the seat
  the player names (`seat`). No free seat: the player (and their stake) waits
  in the queue, in order, and is seated as soon as a seat frees up.
- A seat is **held between hands only if its player bets again** in the "next
  hand" window (`rebet` repeats the last ante). Not betting again frees the
  seat — and seats the next one in the queue.
- `leave` gets a player up at any time between hands (the stake is returned and
  the seat freed on the spot). During a hand it makes them stand on what they
  have, and the seat is freed once the hand is settled.

### Everybody acts at once

An action round is a window (12 s the first time, 8 s after that). In it every
live hand sends **one** choice — `hit`, `stand`, `double`, `split` or
`surrender` — and a choice is final for that round. When everybody has chosen
(or the window ends) the server carries the choices out in **seat order**,
deals the cards they need, and — if any hand is still live — opens the next
round. **No choice by the end of the window means stand.** After `max_rounds`
(8) everyone still in stands.

- **double** takes one card and ends the hand. Its stake (the same as the
  hand's bet) is debited **at once**, when the choice is made.
- **split** (a pair — two cards of the same value, so K-Q splits) debits a
  second stake at once and deals each new hand a second card in the next
  resolve. Up to `max_hands` (4) hands; split aces take one card each unless
  `split_aces_one_card` is off; aces may be resplit only with `resplit_aces`.
  A player with several hands sends one choice per hand (`hand` 1–4).
- **surrender** (off by default) is *late* surrender — it needs the dealer to
  peek, only a first two-card hand can use it — and returns half the bet.
- **insurance** (when Hex shows an ace) costs half the ante, pays 2:1 if Hex
  has blackjack, and is its own window with its own choice (`insurance` /
  `decline`; silence = declined). On a player's blackjack it is "even money".
- A player's own blackjack (a two-card 21 that isn't a split) is paid 3:2 at
  the settle unless Hex has one too (push).
- With **no peek** (`dealer_peeks` off) Hex takes the hole card at the end and
  a dealer blackjack costs only the **original bet**: doubles and splits are
  given back.
- The dealer stands on all 17s (`dealer_hits_soft_17` off) or hits soft 17.
- **Five-card Charlie** (off by default): five cards without busting wins even
  money, unless Hex has blackjack.

Winnings are rounded **down** to whole coins (a blackjack on an odd bet, half a
surrendered bet).

---

## Money — everything is against the bank

Every coin goes through the shared **ledger**, one event per movement:

| type | reason | when |
| --- | --- | --- |
| debit | `bet` | the ante (a seat's first bet of the hand; also a queued player's) |
| debit | `add` | more on the ante before the deal |
| debit | `double` | a double |
| debit | `split` | the second stake of a split |
| debit | `insurance` | insurance (half the ante) |
| credit | `win` | a win, a blackjack, a Charlie (stake included) |
| credit | `push` | a push — the stake back |
| credit | `surrender` | half the stake back |
| credit | `insurance` | insurance paid (3× the insurance stake: stake + 2:1) |
| credit | `refund` | a bet taken back (`/remove`, `/leave`), the original-bet-only refund after a dealer blackjack without a peek, or a stake returned by `/stop`, a restart or a table closing |

The ledger is the same one the other games use: one `seq` numbering, each event
names its `game`. A bank running every game tails
`GET /games/api/ledger?since=<last_seq>` once (or `&game=blackjack` for this
one) — see [The shared ledger](games.md#the-shared-ledger). The `bet_id` reads
`<game id>/h<hand>/s<seat>` (+ `/h<n>` for a hand of a split, `/ins` for
insurance, `/obo` for the original-bet refund, `/q` for the queue).

Before a `/bet`, `/action double` or `/action split`, check the player can
afford it (the debit is Hexcast's word that the stake was taken: debit it in
your bot's bank when you see it in the ledger, or right away from the reply's
`debits`, but never both). Or pass **`balance`** (the player's coins as your
bot knows them) and the call is refused when the cost is more.

### Limits

- `min_bet` applies to a seat's first bet of the hand; `max_bet` (0 = none) to
  the ante (what's down on the seat, adds included). Doubles, splits and
  insurance are not limited by it.
- A table keeps the totals (and the "same as last hand" memory) of its latest 500
  players.

---

## Test games

`/start` with `"test": true` (the panel's **Test table**) plays exactly the same
but **writes nothing to the ledger**: the overlay shows a red `TEST GAME · NO
COINS` plaque on the rail, the coin movements only show in the game's own log,
and test games don't count in the stats. Use them to rehearse without touching
anyone's coins.

---

## HTTP API

Every call returns `{"ok": true|false, ...}`; GET query parameters and a POST
JSON body both work (the body wins). Actions return what they did and the
player's fresh `player` view. Seat and hand numbers are **1-based**.

| Call | What it does |
| --- | --- |
| `GET\|POST /games/api/blackjack/start` | `{seconds (first bet window), seats (1–14), test}` → `{started, seats}`; **409** while a table is open |
| `GET\|POST /games/api/blackjack/bet` | `{user, amount, seat?, balance?}` → `{seat, amount, stake, queued, position, debits:[{user, amount, seq, reason}], player}`; takes a seat (the first free one, or `seat`), else the queue; betting again **adds** to the stake; **409** `bets_closed` outside the betting window |
| `GET\|POST /games/api/blackjack/rebet` | `{user}` (alias `/ditto`) — the same ante as last hand (capped by `max_bet`) |
| `GET\|POST /games/api/blackjack/remove` | `{user, amount?}` — take back part (or all) of the stake before the deal → refund; the seat stays held |
| `GET\|POST /games/api/blackjack/leave` | `{user}` (alias `/cashout`) — before the deal: stake refunded, seat freed (a queued player leaves the queue); during a hand: stands and leaves once it is settled |
| `GET\|POST /games/api/blackjack/action` | `{user, action, hand?, balance?}` — `action`: `hit` (`h`) · `stand` (`s`, `stay`) · `double` (`dd`, `d`) · `split` (`sp`) · `surrender` (`sur`) · `insurance` (`ins`, `even`) · `decline` (`noins`, `no`) → `{action, seat, hand, cost, debits, player}`; **one choice per hand per round, final**; double / split / insurance debit at once; **409** `actions_closed` / `insurance_closed` outside the window, **409** `already chosen this round`. Shortcuts: `/hit /stand /double /split /surrender /insurance` |
| `GET\|POST /games/api/blackjack/next` | end the current window / phase now → `{skipped}` |
| `GET\|POST /games/api/blackjack/close` | close the table after this hand (right away between hands) |
| `GET /games/api/blackjack/table` | the STATE (below) |
| `GET /games/api/blackjack/seats` | the seats and the queue (a light version of the STATE) |
| `GET /games/api/blackjack/user/{name}` | `{player, session, can[], insurance}` — the player's seat, hands, **what they can do** now (`can[]` per live hand: `hit`, `stand`, `double` (cost), `split` (cost), `surrender`, `chosen`) + session debits / credits / net |
| `GET /games/api/blackjack/ledger?since=0&limit=500` | this game's ledger events |
| `GET\|POST /games/api/blackjack/stop` | end the table now: **every stake still on the line is returned** (a hand in progress is a no-contest) |
| `GET /games/api/blackjack/history · last · bets · validate` | finished tables + stats · the last one · rules, payouts, house edge, deck table · dry-run `?user=&amount=&seat=` |
| `GET\|POST /games/api/blackjack/show · hide` | keep the idle table on screen · hide it |
| `GET\|POST /games/api/blackjack/preview` | `{overrides: {appearance keys}, seconds (2–60, default 8)}` (or `x=&y=&scale=`) — the Edit Mode editor's **Test in OBS**: the table goes on screen for `seconds` with that look on top of the saved config (`STATE.preview`), even while hidden. Never touches the game, bets or the ledger; expires on its own; `/hide`, `/stop` and `/preview/clear` end it |
| `GET · POST /games/api/config` | the `"blackjack"` section (every key in [Settings](#settings)) |

`/spin`, `/play`, `/timer`, `/announce` and `/announce/clear` answer 400 for this
game: a table starts with `/start`, and the table-closed card is built in
(its numbers are `STATE.game.summary`).

### STATE

`STATE.game` is `null` when no table is open (`STATE.idle` then has the seats,
rules and limits for the idle table). `STATE.preview` is
`{id, overrides, expires_in_ms}` while a Test in OBS is up, else `null`.

```jsonc
{
  "id": "bj-1a2b3c4d", "test": false,
  "phase": "action",                 // betting | dealing | insurance | action | resolve | dealer | settle | over
  "ends_in_ms": 8400, "phase_ms": 12000, "elapsed_ms": 3600,
  "hand_no": 3, "round": 1, "rounds_max": 8, "closing": false, "seat_count": 10,
  "seats": [                         // one per seat, 1-based `n`
    {"n": 4, "user": "alice", "state": "playing",     // empty | held | bet | playing | done
     "bet": 100, "stake": 200, "ins": 0, "ins_state": null, "leaving": false, "last_bet": 100,
     "hands": [{"uid": 17, "cards": ["8S", "8H"], "bet": 100, "pending": 0, "total": 16, "soft": false,
                "status": "active",  // active | stand | bust | blackjack | charlie | surrender
                "doubled": false, "split": false, "act": "split",      // what it chose this round (null: nothing yet)
                "result": null}]}    // at the settle: {outcome: win|blackjack|charlie|push|surrender|lose|bust, pay, net}
  ],
  "queue": [{"user": "walter", "bet": 50}],
  "dealer": {"name": "Hex", "cards": ["TD", null], "total": 10, "hidden": true, "bj": null, "peeked": true, "up": "TD"},
  "deal": {"kind": "deal", "t0": 350, "step": 240, "order": [[1, 0, 0], [0, 0, 0]], "moves": [], "flip": null, "shuffle": 0},
  "shoe": {"decks": 6, "size": 312, "left": 198, "dealt": 114, "discards": 96, "cut_at": 234, "to_cut": 120,
           "cut_passed": false, "next_decks": 6, "shuffles": 1, "penetration_pct": 75, "shuffled": false},
  "say": {"id": 7, "key": "action", "text": "Hit or stand? Make your move.", "phase": "action"},
  "rules": {"blackjack_pays": "3:2", "dealer_hits_soft_17": false, "...": "...", "text": ["..."], "edge_pct": 0.43},
  "players": [{"user": "alice", "seat": 4, "stake": 200, "status": "16", "hands": 1, "net": 0, "queued": false}],
  "at_risk": 200, "last": {"hand_no": 2, "dealer": {"cards": ["..."], "total": 19}, "results": ["..."], "net": -150},
  "outcome": null, "summary": null,  // set in "over"
  "currency": "hexcoins", "min_bet": 1, "max_bet": 100000, "dealer_name": "Hex", "seats_free": 5
}
```

Cards are two characters: a rank (`A 2–9 T J Q K`) and a suit (`S H D C`) — `TH`
is the ten of hearts. `deal.order` is `[seat, hand, card]` (seat 0 is the
dealer): the order the cards leave the shoe, which is how the overlay times its
animations. The dealer's hole card is `null` until it is turned.

---

## Your bot's side

Hexcast never reads chat. Your bot turns chat commands into API calls and pays
from the ledger. A suggested command set (put yours in the **Commands line**
setting so the overlay shows it):

| Chat | Call |
| --- | --- |
| `!blackjack` (mod) | `POST /start` |
| `!bj 100` | `POST /bet {user, amount: 100}` — takes a seat, or waits in the queue |
| `!bj 100 3` | `POST /bet {user, amount: 100, seat: 3}` |
| `!ditto` / `!rebet` | `POST /rebet {user}` |
| `!hit` · `!stand` · `!double` · `!split` · `!surrender` | `POST /action {user, action}` (add `hand: 2` for a split hand) |
| `!ins` / `!noins` | `POST /action {user, action: "insurance" \| "decline"}` |
| `!takeback` | `POST /remove {user}` |
| `!leave` | `POST /leave {user}` |
| `!hand` | `GET /user/{name}` → your cards and what you can do |

Answer in chat from the reply: `bet` says the seat, or `queued` with the
`position`; `action` returns the `cost`; `/user/{name}` has the hands and
`can[]`.

---

## Settings

| Key | Default | |
| --- | --- | --- |
| `seats` | 10 | seats around the felt (1–14); the rest queue |
| `seat_fill` | center | `center` (from the middle out) or `first` (1, 2, 3 …) |
| `queue_max` | 20 | the waiting list (0–50) |
| `deck_table` | `1:2,3:4,5:6,8:8` | "from N players: M decks" (1–8 decks, N ascending, the first is 1) |
| `penetration_pct` | 75 | the cut card, % of the shoe dealt before a reshuffle (40–95) |
| `blackjack_pays` | 3:2 | `3:2` · `6:5` · `1:1` · `2:1` |
| `dealer_hits_soft_17` · `dealer_peeks` | off · on | the dealer's rules |
| `double_on` · `double_after_split` | any · on | `any` · `9-11` · `10-11` |
| `max_hands` · `resplit_aces` · `split_aces_one_card` | 4 · off · on | splits (`max_hands` 1 = no splitting) |
| `insurance` · `surrender` · `five_card_charlie` | on · off · off | surrender needs the dealer to peek |
| `min_bet` · `max_bet` | 1 · 100000 | the ante, per seat; `max_bet` 0 = none |
| `open_bet_seconds` · `bet_seconds` | 30 · 15 | the first betting window · the "next hand" window |
| `insurance_seconds` | 8 | |
| `action_seconds` · `later_action_seconds` · `max_rounds` | 12 · 8 · 8 | the first action round · the ones after · rounds per hand |
| `settle_seconds` · `summary_seconds` | 7 · 8 | the payout screen · the table-closed card |
| `card_ms` | 240 | the time a card takes out of the shoe (100–800) |
| `idle_windows` | 3 | betting windows in a row with nobody betting close the table |
| `currency` | hexcoins | the name after amounts (your bot's coin), ≤ 24 characters |
| `dealer_name` | Hex | ≤ 24 characters |
| `commands_text` | "" | a line in the limits panel, e.g. `!bj 100 · !hit · !stand · !double · !split` |
| `deal_clip` · `blackjack_clip` · `win_clip` | "" | soundboard clips (play on the base `/overlay`) |
| `title` | "Blackjack" | the plaque on the rail — your branding (≤ 32 characters) ✓ |
| `x` · `y` · `scale` · `theme` | 50 · 50 · 1 · classic | placement (0–100 % · 0–100 % · 0.2–5 × the 1920×1080 table) · `classic`, `neon`, `midnight`, `royal` ✓ |
| `show_rules` · `show_shoe` · `show_captions` | on · on · on | the rules printed on the felt · the shoe, tray and counters · Hex's table talk ✓ |
| `show_players` · `players_max` | on · 8 | the waiting list board · how many it names ✓ |
| `sfx` · `sfx_volume` | on · 0.6 | the overlay's own sounds ✓ |
| `hide_when_idle` | on | off = the idle table stays on screen between games |

✓ = an appearance key: what the Edit Mode editor edits, and what a Test in OBS
(`/preview`) may override.

The rules in force are fixed when a hand is dealt: a change applies from the
**next** hand, never in the middle of one.

### Edit Mode & branding

Turn on **Edit Mode** (the hexbar) and click the Blackjack card (or its
**✎ Edit placement** button): the same placement & look editor as the other
games. The real table on a 16:9 preview of the 1920×1080 stage, fed a sample
hand in progress, so you place what the stream will really show. Drag it (or
use the 3×3 quick grid), **Scale** 0.2–5, **Theme**, **Title**, the rules on the
felt, the shoe and tray, Hex's table talk, the waiting list board and how many
it names, sound and volume — all live in the preview. **▶ Preview** plays a
local demo hand (bets → deal → a few action rounds with a split and a double →
Hex plays → payouts, about 25 s; you can keep dragging while it plays);
**Test in OBS** puts the unsaved look on the real overlay for 8 seconds
(`/preview`); **Save** stores it and restyles every overlay; **Reset** goes back
to the defaults (not saved until Save). Cancel, ×, Esc or a click outside throw
the unsaved changes away.

`title` is text (never HTML), trimmed to 32 characters, shown upper-case on the
plaque, shrunk to fit and ended with "…" if it still doesn't; empty means the
default. Set it in Edit Mode (to see it live) or in the tab's Settings card.

### On screen

Hex stands behind the table (a visor with the Hexcast hex on it, a bow tie and
a name plate on the rail), the shoe at one corner and the discard tray at the
other, a chip rack on each side of the status pill. The rules are printed on
the felt in an arc. Each seat has a betting circle, a name plate with its
number, a chip stack with denominations (a double puts a second stack beside
the first, marked ×2), and its cards fanned above it with a total badge
(`SOFT 17`, `BUST 24`, `BLACKJACK`) and, in an action round, the choice the
player has made (`HIT`, `STAND`, `DOUBLE`, `SPLIT`); the plates of the seats
that haven't chosen yet glow. A split puts its hands side by side (two rows
for three or four). Cards fly out of the shoe, flip as they land, the dealer's
hole card turns over, losing hands dim and bust ones shake, winners glow and
throw sparkles, and at the end of the hand every card is swept into the tray.
Between hands the held seats show an `ANTE UP · 100` prompt with the last bet.
Sound is synthesized in the browser (deal, chips, flip, win, blackjack, bust)
and can be turned off (`sfx`) — set a soundboard clip to play a sound from the
soundboard overlay instead.

The table sizes itself to the seats: a small table gets big cards; at 14 seats
the cards are smaller but every rank and suit is still readable. A long hand
overlaps its cards more (and a split's hands shrink a little) rather than reach
the dealer, the panels, the shoe or the tray.

---

## Storage & restarts

`config/games_blackjack.json` holds the running table (and the last 50 finished
ones + stats). Every change is written before its ledger events, with those
events as a journal, so a crash can't pay twice or lose a stake. The shoe is
**not** saved. A table still running when Hexcast stops is **settled at the next
start-up**: every stake still on the line (the antes, doubles, splits and
insurance of a hand in progress) is returned and the hand is voided — nobody
wins or loses anything from a restart.
