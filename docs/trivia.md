# Trivia — a game-show quiz where chat bets before seeing the question

A hexagon game-show board: the question in a big lozenge, 2–5 options flip in
underneath (A–E), a countdown ring, a ladder of the game's questions. Chat bets
**before** it sees each question (Jeopardy style: only the number, category and
difficulty are shown), then answers. Everyone may answer; only bets get paid.
Right answers pay by difficulty; winners **ride** their whole balance on the
next question for a streak bonus, or **cash out**.

Questions come from [Open Trivia DB](https://opentdb.com) (CC BY-SA 4.0) and
from your **lore** — your channel's own questions. Everything lives under
`/games/api/trivia/*`, and the game has its own tab on the Games panel:
`/games#trivia`.

The board is yours to brand: its title (`title`, default **TRIVIA**) and what it
calls your own questions (`lore_label`, default **Channel Lore**) are settings —
see [Branding](#branding).

---

## At a glance

| | |
| --- | --- |
| Questions | 15 per game (5 easy → 5 medium → 5 hard) |
| Bets | against the bank, placed **before** each question |
| Pays (total) | easy ×1.5 · medium ×2 · hard ×3, +10% per question already won in a row, never more than ×50 the coins put in |
| Timings | 30 s first bet window · 15 s answers · 3 s votes · 5 s reveal · 15 s ride / cash out / bet · 10 s game-over card |
| No bets | a window closing with nobody playing ends the game |
| Repeats | a question isn't asked again for 12 h (setting `repeat_hours`) |
| Typical game | ~11 min for 15 questions |

---

## A game

```
POST /start ─► betting (30 s)   "QUESTION 1 OF 15 · Science · EASY · pays ×1.5"
                 │               rules box · "Bets close in 10!"
                 │               nobody playing when it closes → game over
                 ▼
               question (15 s)   the question + options; POST /answer {user, answer}
                 ▼                (last answer counts; nobody can change it after)
               votes (3 s)       how many picked each option ("ANSWERS LOCKED")
                 ▼
               reveal (5 s)      the right option lights up green, the rest drop away;
                 │               right bettors win, wrong / silent bettors lose
                 ▼
               betting (15 s)    winners: POST /ride or /cashout (neither = cashed out)
                 │               anyone: POST /bet on the next question
                 ▼  … up to 15 questions (the last one's winners are cashed out)
               over (10 s)       winners and totals
```

`POST /games/api/trivia/next` (or the panel's **Skip ▶**) ends the current
phase right away.

Show the votes: `show_votes` = `before_reveal` (the counts appear once answers
lock — the default), `live` (bars move while chat answers) or `off`.

**Game endings** (`outcome`): `complete` · `walked` (nobody left playing) ·
`no_bets` · `no_questions` (none could be had — stakes on the line are cashed
out) · `stopped` · `restart` / `error`.

---

## Money

- **Bet** `{user, amount}` during a betting window — a ledger **debit**. More
  from the same player in the same window adds to it.
- **Right answer:** balance × pays[difficulty] × (1 + bonus × streak), rounded
  down, where streak = questions this stake already won in a row; capped at
  `max_multiplier` × the coins the player put in (0 = no cap).
- **Wrong or no answer:** the stake is lost (the bank keeps the debit).
- **Between questions a winner:**
  - **rides** — the whole balance goes on the next question, streak kept; or
  - **cashes out** — a ledger **credit** of the balance; they may bet fresh
    again right away (streak starts over).
  - does neither → cashed out when the window closes.
- A ride can't be topped up — cash out, then bet again.
- `/cashout` on a bet placed in the current window just takes it back (a
  `refund`).
- After the last question every winner is cashed out.

Example with the defaults: 100 on an easy question → right → 150. Ride on the
next easy one (streak 1: ×1.5 × 1.10) → 247. Ride a medium (streak 2: ×2 × 1.20)
→ 592.

This is a game of **skill**: there's no house edge in the usual sense —
good players win. Tune `pay_*`, `streak_bonus_pct` and `max_multiplier` to taste.

---

## Questions

**Open Trivia DB.** Multiple-choice questions by difficulty (and optionally one
category — the panel lists them), base64-encoded, one call per 5 seconds per IP.
Hexcast fetches question 1 when a game starts (a second or two) and the rest in
the background during the first betting window; unasked questions are kept in a
pool on disk for the next game. A session token keeps OpenTDB from repeating
itself; on top of that every question asked — OpenTDB or lore — goes on an
**asked list** and isn't asked again for `repeat_hours` (12 — one night). The
panel's **New night** button (`POST /asked/clear`) forgets the list.

If OpenTDB is down, games still run on the pool and on your lore; `/start` only
fails (503) when there's no question at all.

The overlay credits OpenTDB (`Questions: Open Trivia DB · CC BY-SA 4.0`)
whenever one of its questions is in the game, as its license asks.

**Lore.** Your own questions, kept in `config/games_trivia_lore.json`:

```json
{"question": "What colour is the streamer's hat?", "correct": "Red",
 "incorrect": ["Blue", "Green", "Gold"], "difficulty": "easy", "category": "Stream history"}
```

`category` is optional: a lore question without one shows the `lore_label`
(**Channel Lore**) as its category. So does one whose category is the old
automatic `"Hex Lore"`, which lore questions got before `lore_label` existed.
In the STATE such a question's `category` is `""` (with `"source": "lore"`): the
overlay and the panel put the label they are showing in its place — during a
**Test in OBS** that is the previewed label, not the saved one.

1–4 wrong answers (2–5 options; `incorrect` may also be `"Blue|Green|Gold"`).
Add them on the panel (one at a time, or paste a JSON list), or with
`POST /games/api/trivia/lore`. Setting `lore`:

| `lore` | |
| --- | --- |
| `mixed` (default) | every `lore_every`th question (5th, 10th, 15th) is lore of the same difficulty (any difficulty if none is left) — when there is some left |
| `only` | a lore night: every question from your lore |
| `off` | OpenTDB only |

Lore questions get a gold badge on the overlay with the `lore_label` on it
(**CHANNEL LORE** by default).

---

## HTTP API

Every call returns `{"ok": true|false, ...}`; GET query parameters and a POST
JSON body both work.

| Call | What it does |
| --- | --- |
| `GET\|POST /games/api/trivia/start` | `{questions (1–50), difficulty (ramp\|easy\|medium\|hard\|mixed), category (OpenTDB id, 0 = any), lore (off\|mixed\|only), seconds, test}` → `{started, state}`; **409** while a game runs; **503** when no question could be had |
| `GET\|POST /games/api/trivia/bet` | `{user, amount}` → `{amount, debits, player}`; **409** `bets_closed` outside a betting window, or when the player has winnings waiting (ride or cash out first) or is riding |
| `GET\|POST /games/api/trivia/answer` | `{user, answer}` — `A`–`E`, `1`–`5` or the option's text → `{answer, changed, bettor}`; anyone may answer, the last one counts; **409** `answers_closed` outside the question |
| `GET\|POST /games/api/trivia/ride` | `{user}` — a winner lets the balance ride |
| `GET\|POST /games/api/trivia/cashout` | `{user}` (alias `/remove`) → `{credits, ledger}` |
| `GET\|POST /games/api/trivia/next` | end the current phase now |
| `GET /games/api/trivia/table` | the STATE (below) |
| `GET /games/api/trivia/user/{name}` | `{player, answer, session}` |
| `GET /games/api/trivia/ledger?since=0&limit=500` | this game's ledger events |
| `GET /games/api/trivia/lore` | every lore question |
| `POST /games/api/trivia/lore` | add one `{question, correct, incorrect, difficulty, category}` or many `{"questions": [...]}` → `{added, rejected}` |
| `GET\|POST /games/api/trivia/lore/remove` | `{id}` or `{ids: [...]}` |
| `GET /games/api/trivia/bank` | the pool per difficulty, lore counts, asked list size, OpenTDB's last error |
| `GET /games/api/trivia/categories` | OpenTDB's categories `[{id, name}]` |
| `GET\|POST /games/api/trivia/asked/clear` | a new night: asked questions may come back |
| `GET\|POST /games/api/trivia/stop` | end the game now: if answers were already locked that question counts; then every stake / balance is credited back |
| `GET\|POST /games/api/trivia/preview` | `{overrides: {appearance keys}, seconds (2–60, default 8)}` (or `x=&y=&scale=`) — the Edit Mode editor's **Test in OBS**: the board goes on screen for `seconds` with that look on top of the saved config (`STATE.preview`), even while hidden, over whatever it shows (the idle card between games). Never touches the game, bets or the ledger; expires on its own; `/hide`, `/stop` and `/preview/clear` end it |
| `GET /games/api/trivia/history · last · bets` | finished games + stats · the last one · rules + pays |
| `GET · POST /games/api/config` | the `"trivia"` section (every key in [Settings](#settings)) |

`/spin`, `/play`, `/timer`, `/announce` and `/announce/clear` answer 400 for
this game — a game starts with `/start`, a look is tried out on the overlay
with `/preview`, and the game-over card is built in (its numbers are
`STATE.game.summary`).

### STATE

`STATE.game` (null between games):

```jsonc
{
  "id": "tv-1a2b3c4d", "test": false,
  "phase": "question",              // betting | question | votes | reveal | over
  "ends_in_ms": 9100, "phase_ms": 15000, "elapsed_ms": 5900,
  "number": 7, "total": 15, "plan": ["easy", "...", "hard"],
  "next": null,                     // betting: {number, category, difficulty, source}; category "" = a lore question without one
  "question": {"number": 7, "category": "Science: Computers", "difficulty": "medium", "source": "opentdb",
               "text": "…", "options": ["…", "…", "…", "…"],
               "answer": null},     // the index, from the reveal on only
  "votes": null,                    // [counts per option] when they're shown
  "voters": 23,
  "players": [{"user": "alice", "status": "in", "balance": 247, "basis": 100, "streak": 2,
               "fresh": false, "if_right": 592, "answered": true}],
  "on_the_line": 247, "waiting": 0,
  "pays": {"easy": 1.5, "medium": 2, "hard": 3, "bonus_pct": 10, "max_multiplier": 50},
  "results": [{"number": 1, "difficulty": "easy", "correct": "B"}],
  "last": {"number": 6, "correct": "C", "votes": [3, 1, 12, 0], "right": [], "wrong": []},
  "outcome": null, "summary": null, "opentdb": true, "currency": "coins"
}
```

Next to `game`, `STATE.idle` has the next game's settings and `STATE.preview` is
`{id, overrides, expires_in_ms}` while a Test in OBS is up (else `null`).

A player's `status`: `in` (a stake on the coming / current question) or `won`
(winnings waiting for ride / cash out). The correct answer never leaves the
server before the reveal — not in the STATE, the panel, or any reply.

### Ledger reasons

| type | reason | when |
| --- | --- | --- |
| debit | `bet` / `add` | a bet / more in the same window |
| credit | `cashout` | a winner cashes out (or is cashed out: no choice, the last question, `/stop`) |
| credit | `refund` | a bet taken back in its own window, or returned by `/stop` |

Same shared ledger as the other games: one `seq` numbering, each event names
its `game`. A bank running every game tails
`GET /games/api/ledger?since=<last_seq>` once (or `&game=trivia` for this
one) — see [The shared ledger](games.md#the-shared-ledger).

---

## Your bot's side

Hexcast never reads chat. Suggested commands (put yours in the **Commands
line** setting so the board shows them):

| Chat | Call |
| --- | --- |
| `!trivia` (mod) | `POST /start` |
| `!bet 100` | `POST /bet {user, amount: 100}` |
| `!a B` / `!b` … | `POST /answer {user, answer: "B"}` |
| `!ride` | `POST /ride {user}` |
| `!cashout` | `POST /cashout {user}` |

Copying: if answers are typed in public chat, others can copy them. Delete
`!a` messages in the bot, take answers by whisper, or keep it as part of the
fun — the vote counts never show before answers lock unless `show_votes` is
`live`.

---

## Settings

| Key | Default | |
| --- | --- | --- |
| `questions` · `difficulty` · `category` | 15 · ramp · 0 | per game |
| `lore` · `lore_every` | mixed · 5 | your own questions |
| `repeat_hours` | 12 | 0 = never repeat until **New night** |
| `open_bet_seconds` · `between_seconds` · `answer_seconds` | 30 · 15 · 15 | windows |
| `show_votes` · `votes_seconds` · `reveal_seconds` · `summary_seconds` | before_reveal · 3 · 5 · 10 | |
| `pay_easy` · `pay_medium` · `pay_hard` | 1.5 · 2 · 3 | total paid back |
| `streak_bonus_pct` · `max_multiplier` | 10 · 50 | 0 = no cap |
| `min_bet` · `max_bet` · `currency` | 1 · 100000 · coins | `currency`: the name after amounts (your bot's coin), ≤ 24 characters |
| `commands_text` | "" | a line on the board, e.g. `!bet 100 · !a B · !ride · !cashout` |
| `question_clip` · `reveal_clip` | "" | soundboard clips |
| `title` | "TRIVIA" | the board's name: header + the card between games (≤ 32 characters) ✓ |
| `lore_label` | "Channel Lore" | what the overlay calls your own questions (≤ 24 characters) ✓ |
| `x` · `y` · `scale` · `theme` | 50 · 50 · 1 · hex | placement (0–100 % · 0–100 % · 0.2–5 × the 1120×630 board) · `hex`, `gameshow`, `neon` ✓ |
| `show_rules` · `show_players` · `players_max` | on · on · 8 | the rules box while bets are open · the players board · how many it lists ✓ |
| `sfx` · `sfx_volume` | on · 0.5 | the overlay's own sounds ✓ |
| `hide_when_idle` | on | off = the idle card stays on screen between games |

✓ = an appearance key: what the Edit Mode editor edits, and what a Test in OBS
(`/preview`) may override.

### Edit Mode

Turn on **Edit Mode** (the hexbar) and click the Trivia card (or its **✎ Edit
placement** button): the same placement & look editor as roulette and craps.
The real board on a 16:9 preview of the 1920×1080 stage, fed a sample game in
its bet window (six sample players, the rules box, a lore question), so you
place what the stream will really show. Drag it (or use the 3×3 quick grid),
**Scale** 0.2–5, **Theme**, **Title**, **Lore** (the `lore_label`), the rules box,
the players board and how many it lists, sound and volume — all live in the
preview. **▶ Preview** plays a local demo question (bets close → the question →
the votes → the reveal → the game-over card, about 17 s; you can keep dragging
while it plays); **Test in OBS** puts the unsaved
look on the real overlay for 8 seconds (`/preview`); **Save** stores it and
restyles every overlay; **Reset** goes back to the defaults (not saved until
Save). Cancel, ×, Esc or a click outside throw the unsaved changes away.

### Branding

Hexcast is platform agnostic: the board carries your name, not ours.
`title` replaces the header's name and the big title of the card shown between
games; `lore_label` names your own questions on the overlay (the gold badge, and
the category of a lore question that has none) and on the panel. Both are text
(never HTML), trimmed to 32 / 24 characters. The header shrinks a long title to fit and ends one that still
doesn't fit with "…"; the card between games shows it whole, and the lore label wraps.
A plain emoji counts as one character; a flag, a skin-tone emoji or a combined
one (a family) counts as several, and one that doesn't fit whole is dropped,
never cut in half. An
empty value means the default. Set them in Edit Mode (to see them live) or in
the tab's Settings card.

---

## Storage & restarts

`config/games_trivia.json` holds the running game (and the last 50 finished +
stats); `config/games_trivia_bank.json` the OpenTDB token, the unasked pool and
the asked list; `config/games_trivia_lore.json` your lore. A game still running
when Hexcast stops is settled at the next start-up: a question whose answers
were locked counts, then every balance still on the table is credited back.
