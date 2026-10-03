"""Blackjack - Hex deals a Vegas shoe game to a table of chatters (Games add-on).
"""

from __future__ import annotations

import re
import secrets
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from hexcast_plugins.games.core import (
    COMMANDS_TEXT_MAX,
    Ledger,
    ROUND_PLAYERS_MAX,
    ROUND_TITLE_MAX,
    RoundGame,
    _RNG,
    _THEMES,
    _aggregate_credits,
    _as_float,
    _clean_user,
    _cut,
    _debits,
    _display_text,
    _ev,
    _flag,
    _ledger_reply,
    _parse_coins,
    _round_common,
    _round_request,
    _strict_int,
    log,
)

# paths + limits that belong to this game
from hexcast_plugins.games.core import CONFIG_DIR

BLACKJACK_PATH = CONFIG_DIR / "games_blackjack.json"      # the running table + history (blackjack)
SEATS_MAX = 14                  # seats around the felt (setting `seats`, 1..SEATS_MAX)
QUEUE_MAX = 50                  # waiting list (setting `queue_max`, 0..QUEUE_MAX)
HANDS_MAX = 4                   # a seat plays at most this many hands after splits
DEALER_NAME_MAX = 24

# --------------------------------------------------------------------------
# blackjack
# --------------------------------------------------------------------------
# A Vegas shoe game. Hex is the dealer and the BANK: every bet is a ledger DEBIT when it goes
# down (the ante, a double, a split, insurance), every payout a ledger CREDIT when the hand is
# settled. Nothing about the cards is the overlay's business: the SERVER shuffles the shoe
# (secrets.SystemRandom), deals every card and decides every hand; the overlay only animates
# the STATE it is given (and never gets the dealer's hole card before it is turned over).
#
# One table, up to `seats` seats in an arc (the overflow waits in a queue). The table plays
# hand after hand (a RoundGame "game" = a session at the table):
#
#   betting   the "next hand" window. First bet takes a seat (the first free one, or `seat`); a
#             seat is only kept if its player bets again; no free seat -> the queue.
#   dealing   shuffle (when a new shoe is needed), then two cards each + the dealer's two.
#   insurance (only when Hex shows an ace) 2:1 on half the bet.
#   action    SIMULTANEOUS rounds, not turn by turn: a window in which every seat sends one
#             choice per hand (hit / stand / double / split / surrender); at the end of the
#             window - or as soon as everybody has chosen - the server resolves them all in
#             seat order. Hands that are still live get another round; silence = stand.
#   resolve   the round's cards are dealt out (animation).
#   dealer    Hex turns the hole card and plays (S17 / H17).
#   settle    results and payouts (credits), chips move.
#   betting   ... and again.
#
# The shoe is built from 2..8 decks by how many players are seated when it is built (the table
# `deck_table`), reshuffled when the cut card is reached, and a shoe that the table has outgrown
# is replaced by a bigger one at the next shuffle (or sooner when it can't cover a hand).

RANKS = "A23456789TJQK"
SUITS = "SHDC"                  # spades, hearts, diamonds, clubs
_POINTS = {"A": 1, "T": 10, "J": 10, "Q": 10, "K": 10, **{str(n): n for n in range(2, 10)}}

STATUSES = ("active", "stand", "bust", "blackjack", "charlie", "surrender")
BJ_PAYS = {"3:2": (3, 2), "6:5": (6, 5), "1:1": (1, 1), "2:1": (2, 1)}
DOUBLE_ON = ("any", "9-11", "10-11")
SEAT_FILL = ("center", "first")
DEFAULT_DECK_TABLE = "1:2,3:4,5:6,8:8"      # "from N players: M decks"
PHASE_WORDS = {"betting": "bets open", "dealing": "dealing", "insurance": "insurance", "action": "your move",
               "resolve": "dealing hits", "dealer": "dealer plays", "settle": "paying out", "over": "table closed"}
SHUFFLE_MS = 3600               # the shuffle animation, when a new shoe is built
DEAL_LEAD_MS = 350              # a beat before the first card
DEAL_TAIL_MS = 650              # ... and after the last one

_OUTCOMES = {"closed": "Table closed", "idle": "Nobody left at the table - closed", "no_bets": "No bets - no game",
             "stopped": "Table stopped - every stake returned", "restart": "Settled after a restart",
             "error": "Settled after an error"}

# Hex's table talk: a small pool per moment (cosmetic - the server picks so every overlay agrees)
SAYS = {
    "open": ["Sit. Wager. See what it costs you.", "The table is open. Bring your coins.", "Come closer, mortals. Place your bets."],
    "next": ["Again? Bet to keep your seat.", "Your seat is warm. Ante up or lose it.", "Another round, another sin. Bet."],
    "shuffle": ["Shuffling a fresh shoe. No cheating, I promise.", "New cards. Same fate.", "The deck is reborn."],
    "deal": ["Here come the cards. Try not to pray.", "Good luck. You will need it.", "Cards are out."],
    "insurance": ["An ace. Care to bet against my luck?", "Ace up. Insure your soul?", "Ace. Insurance pays 2 to 1. Tempting."],
    "peek_bj": ["Blackjack. Delicious.", "Twenty-one for the house. Thank you.", "Oh dear. Blackjack. For me."],
    "peek_ok": ["No blackjack. Play on, if you dare.", "Nothing there. Your move.", "Not this time. Hit or stand."],
    "action": ["Hit or stand? Choose wisely.", "Your move. All of you. Now.", "Decide. I'm patient. Mostly."],
    "more": ["Another round. Still brave?", "Still in it? Decide.", "More cards, or are you done?"],
    "dealer": ["My turn.", "Let's see what I'm hiding.", "Turning my card. Slowly."],
    "bust": ["I bust? Fine. Take your winnings. For now.", "Over 21. The house pays. Reluctantly.", "A rare mercy. Enjoy it."],
    "house": ["The house takes this one.", "Your coins are mine now.", "Better luck next hand. Or not."],
    "players": ["Fine. Collect your winnings.", "You win. How tiresome.", "The table takes this one. Savor it."],
    "closed": ["The table is closed. Until we meet again.", "That's the game. Run along, mortals."],
}


def card_points(card: str) -> int:
    return _POINTS[card[0]]


def is_card(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 2 and v[0] in RANKS and v[1] in SUITS


def hand_value(cards: list[str]) -> tuple[int, bool]:
    """(best total, soft): an ace counts 11 while that doesn't bust the hand (that is a soft total)."""
    total = sum(_POINTS[c[0]] for c in cards)
    if total <= 11 and any(c[0] == "A" for c in cards):
        return total + 10, True
    return total, False


def is_blackjack(cards: list[str]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


def bj_win(bet: int, pays: str) -> int:
    """What a blackjack wins on `bet` (net), rounded down to whole coins."""
    n, d = BJ_PAYS.get(pays, (3, 2))
    return bet * n // d


def rules_text(rules: dict) -> list[str]:
    """The table's rules in words (the same lines the overlay prints on the felt)."""
    out = [f"Blackjack pays {rules['blackjack_pays'].replace(':', ' to ')}",
           "Dealer hits soft 17" if rules["dealer_hits_soft_17"] else "Dealer stands on all 17s",
           "Dealer peeks for blackjack" if rules["dealer_peeks"] else "Dealer takes the hole card at the end "
           "(original bets only on a dealer blackjack)"]
    out.append({"any": "Double on any two cards", "9-11": "Double on 9, 10 or 11 only",
                "10-11": "Double on 10 or 11 only"}[rules["double_on"]])
    out.append("Double after split" if rules["double_after_split"] else "No double after split")
    mh = rules["max_hands"]
    out.append("No splitting" if mh < 2 else f"Split up to {mh} hands")
    if mh >= 2:
        out.append("Split aces: one card each" if rules["split_aces_one_card"] else "Split aces may draw")
        out.append("Aces may be resplit" if rules["resplit_aces"] else "No resplitting aces")
    out.append("Insurance pays 2 to 1" if rules["insurance"] else "No insurance")
    out.append("Late surrender" if rules["surrender"] and rules["dealer_peeks"] else "No surrender")
    if rules["five_card_charlie"]:
        out.append("Five-card Charlie wins")
    return out


# ---- the hand -------------------------------------------------------------------------

def new_hand(uid: int, cards: list[str] | None = None, bet: int = 0, **kw: Any) -> dict:
    """One hand at a seat: its cards, what is riding on it and where it stands."""
    h = {"uid": uid, "cards": list(cards or []), "bet": bet, "base": bet, "doubled": False, "split": False,
         "aces": False, "status": "active", "act": None, "pending": 0, "paid": False, "result": None}
    h.update(kw)
    return h


def can_hit(hand: dict, rules: dict) -> bool:
    if hand["status"] != "active":
        return False
    return not (hand["aces"] and rules["split_aces_one_card"])      # split aces take one card and stop


def can_double(hand: dict, rules: dict) -> bool:
    if hand["status"] != "active" or len(hand["cards"]) != 2:
        return False
    if hand["aces"] and rules["split_aces_one_card"]:
        return False
    if hand["split"] and not rules["double_after_split"]:
        return False
    if rules["double_on"] == "any":
        return True
    total, soft = hand_value(hand["cards"])
    lo = 9 if rules["double_on"] == "9-11" else 10
    return not soft and lo <= total <= 11


def can_split(hand: dict, nhands: int, rules: dict) -> bool:
    """`nhands`: hands this seat has (or will have once its pending splits are made)."""
    if hand["status"] != "active" or len(hand["cards"]) != 2 or nhands >= rules["max_hands"]:
        return False
    if card_points(hand["cards"][0]) != card_points(hand["cards"][1]):
        return False
    return not (hand["aces"] and not rules["resplit_aces"])


def can_surrender(hand: dict, nhands: int, rules: dict) -> bool:
    return bool(rules["surrender"] and rules["dealer_peeks"] and hand["status"] == "active"
                and len(hand["cards"]) == 2 and not hand["split"] and nhands == 1)


def settle_hand(hand: dict, dealer: dict, rules: dict) -> tuple[str, int]:
    """(outcome, credit) of one finished hand against the dealer's final hand
    {total, bust, bj}. The credit is what the bank pays back: stake included (0 = lost)."""
    bet, st = hand["bet"], hand["status"]
    if st == "surrender":
        return "surrender", bet // 2
    if st == "blackjack":
        return ("push", bet) if dealer["bj"] else ("blackjack", bet + bj_win(bet, rules["blackjack_pays"]))
    if st == "bust":
        return "bust", 0
    if dealer["bj"]:
        return "lose", 0
    if st == "charlie":
        return "charlie", bet * 2
    if dealer["bust"]:
        return "win", bet * 2
    total = hand_value(hand["cards"])[0]
    if total > dealer["total"]:
        return "win", bet * 2
    return ("push", bet) if total == dealer["total"] else ("lose", 0)


# ---- the shoe -------------------------------------------------------------------------

class Shoe:
    """`decks` decks, shuffled with the operating system's random source (secrets.SystemRandom),
    and a cut card `penetration_pct` of the way through: when it has been dealt past, the shoe is
    reshuffled before the next hand."""

    def __init__(self, decks: int, penetration_pct: float = 75, exclude: list[str] | None = None, rng=_RNG) -> None:
        cards = [r + s for _ in range(decks) for s in SUITS for r in RANKS]
        for c in exclude or []:
            try:
                cards.remove(c)
            except ValueError:
                pass
        rng.shuffle(cards)
        self.decks = decks
        self.cards = cards
        self.size = len(cards)
        self.pos = 0
        self.cut = int(self.size * penetration_pct / 100)
        self.spent = False          # rebuilt in the middle of a hand: reshuffle before the next one

    @property
    def left(self) -> int:
        return self.size - self.pos

    @property
    def cut_passed(self) -> bool:
        return self.spent or self.pos >= self.cut

    def draw(self) -> str:
        c = self.cards[self.pos]
        self.pos += 1
        return c

    def put_next(self, cards: list[str]) -> None:
        """Put these cards on top of what is left (unit tests; nothing on the HTTP side can call this)."""
        self.cards[self.pos:self.pos] = list(cards)
        self.size += len(cards)


def parse_deck_table(text: Any) -> list[tuple[int, int]] | None:
    """"1:2,3:4,5:6,8:8" -> [(1, 2), (3, 4), (5, 6), (8, 8)]: from that many players, that many decks
    (1..8 decks; thresholds ascending, starting at 1). None if it is not one."""
    if not isinstance(text, str):
        return None
    out: list[tuple[int, int]] = []
    for part in re.split(r"[,;\s]+", text.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d)", part)
        if not m:
            return None
        n, d = int(m.group(1)), int(m.group(2))
        if not 1 <= d <= 8 or not 1 <= n <= 99 or (out and n <= out[-1][0]):
            return None
        out.append((n, d))
    return out if out and out[0][0] == 1 else None


def deck_table_text(table: list[tuple[int, int]]) -> str:
    return ",".join(f"{n}:{d}" for n, d in table)


def decks_for(table: list[tuple[int, int]], players: int) -> int:
    decks = table[0][1]
    for n, d in table:
        if players >= n:
            decks = d
    return decks


def deck_table_view(table: list[tuple[int, int]]) -> list[dict]:
    """[{players: "1-2", from: 1, to: 2, decks: 2}, ...] for the docs and the panel."""
    out = []
    for i, (n, d) in enumerate(table):
        nxt = table[i + 1][0] - 1 if i + 1 < len(table) else None
        out.append({"from": n, "to": nxt, "decks": d,
                    "players": (f"{n}+" if nxt is None else str(n) if nxt == n else f"{n}-{nxt}")})
    return out


# ---- the house edge (basic strategy) -----------------------------------------------------
# Computed, not typed in: an exact infinite-deck enumeration of basic strategy for the table's
# rules (dealer S17 / H17, peek, blackjack pay, doubling limits, DAS, splits up to N hands,
# resplitting / drawing to split aces, late surrender, five-card Charlie), then the usual
# finite-shoe correction of about 0.5 % / decks (single deck -0.5 %, 2 decks -0.25 %, 8 decks
# -0.06 %). Insurance is a side bet (about 7.7 % for the house) and is not part of the figure.
# Split hands are priced one hand at a time (a resplit of one hand is not charged against its
# sibling's resplits) - the error is a few thousandths of a percent.

_CARDS = tuple([(v, 1 / 13) for v in range(1, 10)] + [(10, 4 / 13)])


def _add(total: int, soft: bool, v: int) -> tuple[int, bool]:
    """A hand (total, soft: one ace counts 11) takes a card worth v."""
    if v == 1 and not soft and total + 11 <= 21:
        return total + 11, True
    t = total + v
    if t > 21 and soft:
        return t - 10, False
    return t, soft


@lru_cache(maxsize=None)
def _dealer_final(total: int, soft: bool, h17: bool) -> tuple[float, ...]:
    """The dealer's final hand from (total, soft): P(17), P(18), P(19), P(20), P(21), P(bust)."""
    if total > 21:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    if total > 17 or (total == 17 and not (soft and h17)):
        out = [0.0] * 6
        out[total - 17] = 1.0
        return tuple(out)
    acc = [0.0] * 6
    for v, p in _CARDS:
        t, s = _add(total, soft, v)
        sub = _dealer_final(t, s, h17)
        for i in range(6):
            acc[i] += p * sub[i]
    return tuple(acc)


@lru_cache(maxsize=None)
def dealer_distribution(up: int, h17: bool) -> tuple[tuple[float, ...], float]:
    """((P(17..21), P(bust)) given the dealer has NO blackjack, P(blackjack)) for an upcard worth `up`
    (1 = ace .. 10)."""
    bjc = 10 if up == 1 else 1 if up == 10 else None
    q = 0.0 if bjc is None else dict(_CARDS)[bjc]
    t0, s0 = _add(0, False, up)
    acc = [0.0] * 6
    for v, p in _CARDS:
        if v == bjc:
            continue
        sub = _dealer_final(*_add(t0, s0, v), h17)
        for i in range(6):
            acc[i] += p * sub[i]
    return tuple(a / (1 - q) for a in acc), q


@lru_cache(maxsize=256)
def _edge_inf(h17: bool, bj_n: int, bj_d: int, double_on: str, das: bool, max_hands: int, rsa: bool,
              ace_one: bool, surrender: bool, charlie: bool) -> float:
    dist = {up: dealer_distribution(up, h17) for up in range(1, 11)}
    bj_ratio = bj_n / bj_d
    memo: dict = {}
    smemo: dict = {}

    def can_dbl(total: int, soft: bool) -> bool:
        if double_on == "any":
            return True
        return not soft and (9 if double_on == "9-11" else 10) <= total <= 11

    def stand(total: int, up: int) -> float:
        d = dist[up][0]
        ev = d[5]
        for i in range(5):
            ev += d[i] * (1 if total > 17 + i else 0 if total == 17 + i else -1)
        return ev

    def best(total: int, soft: bool, n: int, up: int, dbl: bool) -> float:
        key = (total, soft, n, up, dbl)
        if key in memo:
            return memo[key]
        r = stand(total, up)
        if total < 21:
            hit = 0.0
            for v, p in _CARDS:
                t, s = _add(total, soft, v)
                if t > 21:
                    hit -= p
                elif charlie and n + 1 >= 5:
                    hit += p
                else:
                    hit += p * best(t, s, n + 1, up, False)
            r = max(r, hit)
            if dbl and can_dbl(total, soft):
                d = 0.0
                for v, p in _CARDS:
                    t, s = _add(total, soft, v)
                    d += p * (-1 if t > 21 else stand(t, up))
                r = max(r, 2 * d)
        memo[key] = r
        return r

    def after_split(v: int, c: int, up: int, nh: int) -> float:
        """One hand (v, c) after a split, nh hands at the table now."""
        t, s = _add(*_add(0, False, v), c)
        base = stand(t, up) if (v == 1 and ace_one) else best(t, s, 2, up, das)
        if c == v and nh < max_hands and (v != 1 or rsa):
            base = max(base, split_value(v, up, nh))
        return base

    def split_value(v: int, up: int, nh: int) -> float:
        """Both hands of splitting a pair of v when nh hands exist now (nh < max_hands)."""
        key = (v, up, nh)
        if key not in smemo:
            smemo[key] = 2 * sum(p * after_split(v, c, up, nh + 1) for c, p in _CARDS)
        return smemo[key]

    total_ev = 0.0
    for up in range(1, 11):
        pu = 4 / 13 if up == 10 else 1 / 13
        q = dist[up][1]
        ev_up = 0.0
        for x, px in _CARDS:
            for y, py in _CARDS:
                t, s = _add(*_add(0, False, x), y)
                if t == 21:
                    cond, lose_bj = bj_ratio, 0.0           # a dealer blackjack pushes it
                else:
                    cond = best(t, s, 2, up, True)
                    if x == y and max_hands >= 2:
                        cond = max(cond, split_value(x, up, 1))
                    if surrender:
                        cond = max(cond, -0.5)
                    lose_bj = -1.0
                ev_up += px * py * ((1 - q) * cond + q * lose_bj)
        total_ev += pu * ev_up
    return -total_ev * 100


def house_edge(rules: dict, decks: int | None = None) -> float:
    """The house edge in percent (basic strategy, no insurance). `decks` None = an infinite shoe."""
    e = _edge_inf(bool(rules["dealer_hits_soft_17"]), *BJ_PAYS[rules["blackjack_pays"]], rules["double_on"],
                  bool(rules["double_after_split"]), int(rules["max_hands"]), bool(rules["resplit_aces"]),
                  bool(rules["split_aces_one_card"]), bool(rules["surrender"] and rules["dealer_peeks"]),
                  bool(rules["five_card_charlie"]))
    return e - (0.5 / decks if decks else 0.0)


# ---- config --------------------------------------------------------------------------------

RULE_KEYS = ("dealer_hits_soft_17", "dealer_peeks", "blackjack_pays", "double_on", "double_after_split",
             "max_hands", "resplit_aces", "split_aces_one_card", "insurance", "surrender", "five_card_charlie")

_ACTIONS = {"hit": "hit", "h": "hit", "stand": "stand", "s": "stand", "stay": "stand", "double": "double",
            "dd": "double", "d": "double", "doubledown": "double", "split": "split", "sp": "split",
            "surrender": "surrender", "sur": "surrender", "insurance": "insurance", "ins": "insurance",
            "even": "insurance", "evenmoney": "insurance", "decline": "decline", "noinsurance": "decline",
            "noins": "decline", "no": "decline"}


def rules_of(cfg: dict) -> dict:
    return {k: cfg[k] for k in RULE_KEYS}


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def seat_order(n: int, fill: str) -> list[int]:
    """The order free seats are handed out in (0-based). `center`: from the middle outwards, so a
    small table sits in the middle of the felt; `first`: seat 1, 2, 3 ..."""
    if fill == "first":
        return list(range(n))
    lo, hi = (n - 1) // 2, n // 2
    out: list[int] = []
    if lo == hi:
        out.append(lo)
        lo, hi = lo - 1, hi + 1
    while lo >= 0 or hi < n:
        if hi < n:
            out.append(hi)
            hi += 1
        if lo >= 0:
            out.append(lo)
            lo -= 1
    return out


class Blackjack(RoundGame):
    key = "blackjack"
    title = "Blackjack"
    id_prefix = "bj"
    PHASES = ("betting", "dealing", "insurance", "action", "resolve", "dealer", "settle", "over")
    STAT_KEYS = ("games", "hands", "player_hands", "blackjacks", "dealer_blackjacks", "busts", "dealer_busts",
                 "doubles", "splits", "insurance", "wins", "pushes", "losses", "shoes", "no_bets", "stopped",
                 "total_bet", "total_paid", "house_net")
    CLIP_KEYS = ("deal_clip", "blackjack_clip", "win_clip")
    TALLY_KEYS = ("hands", "player_hands", "blackjacks", "dealer_blackjacks", "busts", "dealer_busts", "doubles",
                  "splits", "insurance", "wins", "pushes", "losses", "shoes")

    DEFAULTS: dict[str, Any] = {
        # placement: table centre in % of the 1920x1080 stage; scale x the 1920x1080 table
        "x": 50, "y": 50, "scale": 1.0,
        "theme": "classic",             # classic | neon | midnight | royal
        "title": "Blackjack",           # the plaque on the table (your branding)
        "show_rules": True,             # the rules printed on the felt
        "show_players": True, "players_max": 8,     # the waiting list board
        "show_shoe": True,              # the shoe, the discard tray and their counters
        "show_captions": True,          # the dealer's table talk
        "sfx": True, "sfx_volume": 0.6,
        "hide_when_idle": True,
        "commands_text": "",            # e.g. "!bj 100 · !hit · !stand · !double" (your bot's commands)
        "dealer_name": "Hex",
        "currency": "hexcoins",
        "min_bet": 1, "max_bet": 100000,  # the ante, per seat; 0 = no max
        "seats": 10,                    # seats around the felt (1..14); the rest queue
        "seat_fill": "center",          # center | first
        "queue_max": 20,                # how many wait for a seat
        "deck_table": DEFAULT_DECK_TABLE,   # the shoe grows with the table: "from N players: M decks"
        "penetration_pct": 75,          # the cut card
        # the rules
        "dealer_hits_soft_17": False, "dealer_peeks": True, "blackjack_pays": "3:2",
        "double_on": "any", "double_after_split": True, "max_hands": 4,
        "resplit_aces": False, "split_aces_one_card": True,
        "insurance": True, "surrender": False, "five_card_charlie": False,
        # timing
        "open_bet_seconds": 30,         # the first betting window
        "bet_seconds": 15,              # the "next hand" window: bet again to keep your seat
        "insurance_seconds": 8,
        "action_seconds": 12,           # the first action round
        "later_action_seconds": 8,      # the rounds after it
        "max_rounds": 8,                # action rounds per hand (then everybody still in stands)
        "settle_seconds": 7,
        "summary_seconds": 8,           # the table-closed card
        "card_ms": 240,                 # one card out of the shoe
        "idle_windows": 3,              # betting windows in a row with nobody betting close the table
        "deal_clip": "", "blackjack_clip": "", "win_clip": "",       # soundboard clips
    }
    SCHEMA: dict[str, tuple] = {
        "x": ("num", 0, 100), "y": ("num", 0, 100), "scale": ("num", 0.2, 5),
        "theme": ("enum", _THEMES), "title": ("name", ROUND_TITLE_MAX),
        "show_rules": ("bool",), "show_players": ("bool",), "players_max": ("int", 1, 20),
        "show_shoe": ("bool",), "show_captions": ("bool",),
        "sfx": ("bool",), "sfx_volume": ("num", 0, 1),
        "hide_when_idle": ("bool",), "commands_text": ("str", COMMANDS_TEXT_MAX),
        "dealer_name": ("name", DEALER_NAME_MAX), "currency": ("name", 24),
        "min_bet": ("int", 1, 1e9), "max_bet": ("int", 0, 1e12),
        "seats": ("int", 1, SEATS_MAX), "seat_fill": ("enum", SEAT_FILL), "queue_max": ("int", 0, QUEUE_MAX),
        "deck_table": ("str", 40), "penetration_pct": ("num", 40, 95),
        "dealer_hits_soft_17": ("bool",), "dealer_peeks": ("bool",), "blackjack_pays": ("enum", tuple(BJ_PAYS)),
        "double_on": ("enum", DOUBLE_ON), "double_after_split": ("bool",), "max_hands": ("int", 1, HANDS_MAX),
        "resplit_aces": ("bool",), "split_aces_one_card": ("bool",),
        "insurance": ("bool",), "surrender": ("bool",), "five_card_charlie": ("bool",),
        "open_bet_seconds": ("num", 5, 300), "bet_seconds": ("num", 5, 300), "insurance_seconds": ("num", 3, 60),
        "action_seconds": ("num", 4, 120), "later_action_seconds": ("num", 3, 120), "max_rounds": ("int", 1, 20),
        "settle_seconds": ("num", 3, 60), "summary_seconds": ("num", 3, 60), "card_ms": ("int", 100, 800),
        "idle_windows": ("int", 1, 50),
        "deal_clip": ("str", 200), "blackjack_clip": ("str", 200), "win_clip": ("str", 200),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "title", "show_rules", "show_players", "players_max", "show_shoe",
                  "show_captions", "sfx", "sfx_volume")

    def __init__(self, path: Path | None = None, ledger: Ledger | None = None) -> None:
        self.shoe: Shoe | None = None       # in memory only: a table that is restarted refunds its hand anyway
        super().__init__(path, ledger)

    def default_path(self) -> Path:
        return BLACKJACK_PATH

    def validate_config(self, raw: Any) -> dict:
        out = super().validate_config(raw)
        if out["max_bet"] and out["max_bet"] < out["min_bet"]:
            out["max_bet"] = 0
        table = parse_deck_table(out["deck_table"])
        out["deck_table"] = deck_table_text(table or parse_deck_table(DEFAULT_DECK_TABLE))
        return out

    # ---- saved game -----------------------------------------------------------------

    @staticmethod
    def _clean_hand(raw: Any) -> dict | None:
        if not isinstance(raw, dict):
            return None
        cards = [c for c in raw.get("cards") or [] if is_card(c)][:20]
        bet = max(0, _strict_int(raw.get("bet")) or 0)
        res = raw.get("result")
        status = raw.get("status") if raw.get("status") in STATUSES else "stand"
        act = raw.get("act") if raw.get("act") in ("hit", "stand", "double", "split", "surrender") else None
        return new_hand(max(0, _strict_int(raw.get("uid")) or 0), cards, bet, base=max(0, _strict_int(raw.get("base")) or bet),
                        doubled=bool(raw.get("doubled")), split=bool(raw.get("split")), aces=bool(raw.get("aces")),
                        status=status, act=act, pending=max(0, _strict_int(raw.get("pending")) or 0),
                        paid=bool(raw.get("paid")), result=res if isinstance(res, dict) else None)

    @classmethod
    def _clean_seat(cls, raw: Any) -> dict | None:
        if not isinstance(raw, dict):
            return None
        user = _clean_user(raw.get("user"))
        if not user:
            return None
        hands = [h for h in map(cls._clean_hand, raw.get("hands") if isinstance(raw.get("hands"), list) else []) if h][:HANDS_MAX]
        return {"user": user, "bet": max(0, _strict_int(raw.get("bet")) or 0), "ins": max(0, _strict_int(raw.get("ins")) or 0),
                "ins_state": raw.get("ins_state") if raw.get("ins_state") in ("taken", "declined") else None,
                "ins_paid": bool(raw.get("ins_paid")), "leaving": bool(raw.get("leaving")), "hands": hands,
                "sat_at": _as_float(raw.get("sat_at")) or time.time()}

    def clean_game(self, raw: Any) -> dict | None:
        g = _round_common(raw, self.PHASES)
        if g is None:
            return None
        n = _clamp(_strict_int(raw.get("seat_count")) or 10, 1, SEATS_MAX)
        sr = raw.get("seats") if isinstance(raw.get("seats"), list) else []
        seats = [self._clean_seat(sr[i]) if i < len(sr) else None for i in range(n)]
        queue = []
        for e in raw.get("queue") if isinstance(raw.get("queue"), list) else []:
            u = _clean_user(e.get("user")) if isinstance(e, dict) else None
            b = _strict_int(e.get("bet")) if isinstance(e, dict) else None
            if u and b and b > 0:
                queue.append({"user": u, "bet": b, "at": _as_float(e.get("at")) or time.time()})
        d = raw.get("dealer") if isinstance(raw.get("dealer"), dict) else None
        dealer = None
        if d is not None:
            dealer = {"cards": [c for c in d.get("cards") or [] if is_card(c)][:20], "reveal": bool(d.get("reveal")),
                      "bj": bool(d.get("bj")), "peeked": d.get("peeked") if isinstance(d.get("peeked"), bool) else None}
        rules = raw.get("rules") if isinstance(raw.get("rules"), dict) else {}
        defaults = rules_of(self.DEFAULTS)
        tally = raw.get("tally") if isinstance(raw.get("tally"), dict) else {}
        g.update({
            "seat_count": n, "hand_no": max(0, _strict_int(raw.get("hand_no")) or 0),
            "round": max(0, _strict_int(raw.get("round")) or 0), "closing": bool(raw.get("closing")),
            "idle": max(0, _strict_int(raw.get("idle")) or 0), "settled": bool(raw.get("settled")),
            "seats": seats, "queue": queue[:QUEUE_MAX], "dealer": dealer, "shuffled": bool(raw.get("shuffled")),
            "last_bets": {u: a for u, a in ((_clean_user(k), _strict_int(v)) for k, v in
                                            (raw.get("last_bets") or {}).items() if isinstance(raw.get("last_bets"), dict))
                          if u and a and a > 0},
            "rules": {k: (rules[k] if isinstance(rules.get(k), type(v)) else v) for k, v in defaults.items()},
            "tally": {k: max(0, _strict_int(tally.get(k)) or 0) for k in self.TALLY_KEYS},
            "deal": None, "say": None, "say_n": max(0, _strict_int(raw.get("say_n")) or 0),
            "uid": max(1, _strict_int(raw.get("uid")) or 1), "len": max(0, _strict_int(raw.get("len")) or 0),
        })
        return g

    # ---- small helpers ------------------------------------------------------------------

    def _seats(self) -> list[dict | None]:
        return self.g["seats"]

    def _seat_index(self, user: str) -> int | None:
        for i, s in enumerate(self.g["seats"]):
            if s and s["user"] == user:
                return i
        return None

    def _queue_index(self, user: str) -> int | None:
        for i, e in enumerate(self.g["queue"]):
            if e["user"] == user:
                return i
        return None

    def _next_uid(self) -> int:
        u = self.g["uid"]
        self.g["uid"] = u + 1
        return u

    def _free_seat(self, want: int | None) -> int | None:
        """A free seat (0-based): `want` if it is free, else the first free one in the seating order."""
        g = self.g
        if want is not None:
            return want if g["seats"][want] is None else None
        for i in seat_order(g["seat_count"], self.cfg["seat_fill"]):
            if g["seats"][i] is None:
                return i
        return None

    def _new_seat(self, user: str, bet: int) -> dict:
        return {"user": user, "bet": bet, "ins": 0, "ins_state": None, "ins_paid": False, "leaving": False,
                "hands": [], "sat_at": round(time.time(), 3)}

    def _phase(self, phase: str, seconds: float | None) -> None:
        """_set_phase, remembering the window's length (an early close shortens `ends_at` only)."""
        self._set_phase(phase, seconds)
        self.g["len"] = int(round(seconds * 1000)) if seconds is not None else 0

    def _say(self, key: str, **kw: Any) -> None:
        lines = SAYS.get(key)
        g = self.g
        if not lines or g is None:
            return
        g["say_n"] += 1
        g["say"] = {"id": g["say_n"], "key": key, "text": secrets.choice(lines).format(**kw), "phase": g["phase"]}

    def _shorten(self, seconds: float) -> None:
        """End the current window in `seconds` (everybody has answered)."""
        g = self.g
        at = round(time.time() + seconds, 3)
        if g.get("ends_at") is not None and at < g["ends_at"]:
            g["ends_at"] = at
            self._arm(at)

    def _in_play(self) -> list[str]:
        """Every card on the felt right now (they are not in the shoe and not in the discard tray)."""
        g = self.g
        cards: list[str] = []
        if g is None:
            return cards
        for s in g["seats"]:
            if s:
                for h in s["hands"]:
                    cards += h["cards"]
        if g.get("dealer"):
            cards += g["dealer"]["cards"]
        return cards

    def _players_now(self) -> int:
        return sum(1 for s in self.g["seats"] if s and s["bet"] > 0)

    def _on_line(self, seat: dict) -> int:
        """Coins of this seat that are riding right now (so a stop / restart can give them back)."""
        if seat["hands"]:
            n = sum(h["bet"] + h["pending"] for h in seat["hands"] if not h["paid"])
            return n + (0 if seat["ins_paid"] else seat["ins"])
        return seat["bet"] if self.g["phase"] == "betting" else 0

    # ---- money --------------------------------------------------------------------------

    def _stake_check(self, current: int, amt: int) -> str | None:
        cfg = self.cfg
        if current == 0 and amt < cfg["min_bet"]:
            return f"minimum bet is {cfg['min_bet']} {cfg['currency']}"
        if cfg["max_bet"] and current + amt > cfg["max_bet"]:
            extra = f" ({current} already down)" if current else ""
            return f"max bet is {cfg['max_bet']} {cfg['currency']}{extra}"
        return None

    @staticmethod
    def _afford(params: dict, cost: int, currency: str) -> str | None:
        """`balance` (optional): the player's coins as the bot knows them - a cost above it is refused."""
        if params.get("balance") in (None, ""):
            return None
        bal = _as_float(params.get("balance"))
        if bal is not None and cost > bal:
            return f"not enough {currency}: needs {cost}, has {int(bal)}"
        return None

    def _refund_events(self) -> list[dict]:
        """Ledger events that give back every coin still on the line (stop / restart / table closed)."""
        g = self.g
        if g is None:
            return []
        events = []
        gid = g["id"]
        for i, s in enumerate(g["seats"]):
            if not s:
                continue
            if s["hands"]:
                for j, h in enumerate(s["hands"]):
                    amt = 0 if h["paid"] else h["bet"] + h["pending"]
                    if amt > 0:
                        events.append(_ev("credit", s["user"], amt, "refund", self._bid(i, tail=f"/h{j + 1}"),
                                          f"Hand {j + 1} (game stopped)", gid))
                if s["ins"] and not s["ins_paid"]:
                    events.append(_ev("credit", s["user"], s["ins"], "refund", self._bid(i, tail="/ins"),
                                      "Insurance (game stopped)", gid))
            elif s["bet"] > 0 and g["phase"] == "betting":
                events.append(_ev("credit", s["user"], s["bet"], "refund", self._bid(i), "Bet (game stopped)", gid))
        for e in g["queue"]:
            events.append(_ev("credit", e["user"], e["bet"], "refund", self._bid(None), "Bet (game stopped)", gid))
        return events

    def open_settlement(self) -> list[dict]:
        events = self._refund_events()
        g = self.g
        for s in g["seats"]:                # whatever was given back is not on the line any more
            if s:
                s["bet"] = 0 if g["phase"] == "betting" else s["bet"]
                for h in s["hands"]:
                    h["paid"] = True
                    h["pending"] = 0
                s["ins_paid"] = True
        g["queue"] = []
        return events

    # ---- API: start / close -------------------------------------------------------------------

    def start_game(self, params: dict) -> tuple[int, dict]:
        if self.g is not None:
            return 409, {"error": "a table is already open", "phase": self.phase}
        cfg = self.cfg
        n = _as_float(params.get("seats"))
        n = _clamp(int(n), 1, SEATS_MAX) if n is not None else cfg["seats"]
        now = time.time()
        self.shoe = None
        self.g = {
            "id": f"bj-{secrets.token_hex(4)}", "test": _flag(params.get("test")), "phase": "betting",
            "started_at": round(now, 3), "phase_at": round(now, 3), "ends_at": None,
            "debits": 0, "credits": 0, "totals": {}, "log": [], "ever_bet": False,
            "outcome": None, "summary": None, "last": None, "currency": cfg["currency"],
            "seat_count": n, "hand_no": 0, "round": 0, "closing": False, "idle": 0, "settled": False,
            "seats": [None] * n, "queue": [], "last_bets": {}, "dealer": None, "deal": None, "say": None, "say_n": 0,
            "rules": rules_of(cfg), "tally": {k: 0 for k in self.TALLY_KEYS}, "uid": 1, "len": 0, "shuffled": False,
        }
        self.hidden = False
        secs = _as_float(params.get("seconds"))
        secs = min(300.0, max(5.0, secs)) if secs is not None else float(cfg["open_bet_seconds"])
        self._phase("betting", secs)
        self._say("open")
        self.save()
        return 200, {"started": self.g["id"], "seats": n}

    def close(self, params: dict) -> tuple[int, dict]:
        """/close: the table closes after this hand (right away between hands)."""
        g = self.g
        if g is None:
            return 409, {"error": "no game running - start one with /start"}
        if g["phase"] in ("betting", "over"):
            if g["phase"] == "betting":
                self._finish("closed")
            return 200, {"closing": False, "closed": True}
        g["closing"] = True
        return 200, {"closing": True, "after": "this hand"}

    # ---- API: betting -------------------------------------------------------------------------

    def _parse_seat(self, v: Any) -> tuple[int | None, str | None]:
        """A requested seat (1-based in the API) -> 0-based index."""
        if v in (None, ""):
            return None, None
        f = _as_float(v)
        n = self.g["seat_count"]
        if f is None or not f.is_integer() or not 1 <= int(f) <= n:
            return None, f"seat must be 1-{n}"
        return int(f) - 1, None

    def place(self, params: dict) -> tuple[int, dict]:
        """/bet: the ante (or more on it) before the deal. Takes a seat, else a place in the queue."""
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        amt, err = _parse_coins(params.get("amount"))
        if err:
            return 400, {"error": err}
        want, err = self._parse_seat(params.get("seat"))
        if err:
            return 400, {"error": err}
        cfg = self.cfg
        si, qi = self._seat_index(user), self._queue_index(user)
        seat_i = si                                   # the seat this stake goes to (None: the queue)
        if si is not None:
            cur = g["seats"][si]["bet"]
        else:
            cur = g["queue"][qi]["bet"] if qi is not None else 0
            seat_i = self._free_seat(want if qi is None else None)
            if seat_i is None:
                if qi is None and want is not None and g["seats"][want] is not None:
                    return 400, {"error": f"seat {want + 1} is taken by @{g['seats'][want]['user']}"}
                if qi is None and len(g["queue"]) >= cfg["queue_max"]:
                    return 400, {"error": f"the table is full ({g['seat_count']} seats, {len(g['queue'])} waiting)"}
        err = self._stake_check(cur, amt) or self._afford(params, amt, cfg["currency"])
        if err:
            return 400, {"error": err}
        if seat_i is None:                            # no free seat: wait in the queue, stake down
            if qi is None:
                g["queue"].append({"user": user, "bet": 0, "at": round(time.time(), 3)})
                qi = len(g["queue"]) - 1
            g["queue"][qi]["bet"] += amt
            total, label = g["queue"][qi]["bet"], "Bet · waiting for a seat"
        else:
            if si is None:                            # (a waiting player brings their stake along)
                carried = g["queue"].pop(qi)["bet"] if qi is not None else 0
                g["seats"][seat_i] = self._new_seat(user, carried)
            seat = g["seats"][seat_i]
            seat["bet"] += amt
            seat["leaving"] = False
            total, label = seat["bet"], f"Bet · seat {seat_i + 1}"
        g["ever_bet"] = True
        g["idle"] = 0
        logged = self._persist([_ev("debit", user, amt, "add" if cur else "bet", self._bid(seat_i, user), label, g["id"])])
        queued = seat_i is None
        return 200, {"seat": None if queued else seat_i + 1, "amount": amt, "stake": total, "queued": queued,
                     "position": (self._queue_index(user) + 1) if queued else None,
                     "debits": _debits(logged), "player": self._player(user)}

    def _bid(self, seat_i: int | None, user: str = "", tail: str = "") -> str:
        """The ledger's bet_id of something at a seat: <game>/h<hand>/s<seat>[/tail] (q = the queue)."""
        g = self.g
        hn = g["hand_no"] + (1 if g["phase"] == "betting" else 0)
        return f"{g['id']}/h{hn}/{'q' if seat_i is None else 's' + str(seat_i + 1)}{tail}"

    def rebet(self, params: dict) -> tuple[int, dict]:
        """/rebet (ditto): the same ante as last hand."""
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        si, qi = self._seat_index(user), self._queue_index(user)
        have = g["seats"][si]["bet"] if si is not None else g["queue"][qi]["bet"] if qi is not None else 0
        if have:
            return 400, {"error": f"@{user} already has {have} down - use /bet to add to it"}
        amt = g["last_bets"].get(user)
        if not amt:
            return 400, {"error": f"@{user} has no earlier bet to repeat"}
        cfg = self.cfg
        if cfg["max_bet"] and amt > cfg["max_bet"]:
            amt = cfg["max_bet"]
        return self.place({**params, "amount": max(amt, cfg["min_bet"])})

    def remove(self, params: dict) -> tuple[int, dict]:
        """/remove: take back part (or all) of the stake before the deal. The seat stays held."""
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        si, qi = self._seat_index(user), self._queue_index(user)
        if si is None and qi is None:
            return 400, {"error": f"@{user} is not at the table"}
        cur = g["seats"][si]["bet"] if si is not None else g["queue"][qi]["bet"]
        if cur <= 0:
            return 400, {"error": f"@{user} has nothing down"}
        if params.get("amount") in (None, ""):
            amt = cur
        else:
            amt, err = _parse_coins(params.get("amount"))
            if err:
                return 400, {"error": err}
            if amt > cur:
                return 400, {"error": f"only {cur} is down"}
        left = cur - amt
        if 0 < left < self.cfg["min_bet"]:
            return 400, {"error": f"what stays down must be at least {self.cfg['min_bet']} - or take it all back"}
        label = "Bet (taken back)"
        if si is not None:
            g["seats"][si]["bet"] = left
            bid = self._bid(si)
        else:
            g["queue"][qi]["bet"] = left
            bid = self._bid(None)
            if left == 0:
                g["queue"].pop(qi)
        logged = self._persist([_ev("credit", user, amt, "refund", bid, label, g["id"])])
        return 200, {"credits": _aggregate_credits(logged, "amount"), "ledger": logged, "stake": left}

    def leave(self, params: dict) -> tuple[int, dict]:
        """/leave: get up. Before the deal the stake comes back and the seat is free at once; during a
        hand the player stands on whatever they have and the seat is freed once it is settled."""
        g = self.g
        if g is None:
            return 409, {"error": "no game running - start one with /start"}
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        qi = self._queue_index(user)
        if qi is not None:
            e = g["queue"].pop(qi)
            logged = self._persist([_ev("credit", user, e["bet"], "refund", self._bid(None), "Bet (left the queue)", g["id"])])
            return 200, {"left": True, "credits": _aggregate_credits(logged, "amount"), "ledger": logged}
        si = self._seat_index(user)
        if si is None:
            return 400, {"error": f"@{user} is not at the table"}
        seat = g["seats"][si]
        if g["phase"] == "betting":
            events = []
            if seat["bet"] > 0:
                events.append(_ev("credit", user, seat["bet"], "refund", self._bid(si), "Bet (left the table)", g["id"]))
            g["seats"][si] = None
            self._promote_queue()
            logged = self._persist(events)
            return 200, {"left": True, "credits": _aggregate_credits(logged, "amount"), "ledger": logged}
        seat["leaving"] = True
        if g["phase"] in ("action", "insurance"):
            self._maybe_close_window()
        self.save()
        return 200, {"left": False, "leaving": "after this hand", "credits": [], "ledger": []}

    def _promote_queue(self) -> None:
        """Seat the waiting players (in order) in whatever seats are free."""
        g = self.g
        while g["queue"]:
            i = self._free_seat(None)
            if i is None:
                return
            e = g["queue"].pop(0)
            g["seats"][i] = self._new_seat(e["user"], e["bet"])

    # ---- API: acting --------------------------------------------------------------------------

    def _hand_arg(self, seat: dict, v: Any) -> tuple[int | None, str | None]:
        """The hand a request is about (1-based in the API; none given: the first live hand that hasn't chosen)."""
        hands = seat["hands"]
        if v in (None, ""):
            for i, h in enumerate(hands):
                if h["status"] == "active" and not h["act"]:
                    return i, None
            for i, h in enumerate(hands):
                if h["status"] == "active":
                    return i, None
            return None, "no live hand"
        f = _as_float(v)
        if f is None or not f.is_integer() or not 1 <= int(f) <= len(hands):
            return None, f"hand must be 1-{len(hands)}"
        return int(f) - 1, None

    def act(self, params: dict) -> tuple[int, dict]:
        """/action: hit | stand | double | split | surrender (an action round), insurance | decline (the
        insurance window). One choice per hand per round; it is final, and money moves at once."""
        g = self.g
        if g is None:
            return self._closed("actions")
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        raw = re.sub(r"[\s_\-!']", "", str(params.get("action", params.get("move", "")) or "")).lower()
        action = _ACTIONS.get(raw)
        if action is None:
            return 400, {"error": "action must be hit, stand, double, split, surrender, insurance or decline"}
        si = self._seat_index(user)
        if si is None:
            if self._queue_index(user) is not None:
                return 400, {"error": f"@{user} is waiting for a seat"}
            return 400, {"error": f"@{user} is not at the table"}
        seat, phase, rules = g["seats"][si], g["phase"], g["rules"]
        if action in ("insurance", "decline"):
            return self._insurance(si, seat, action, params)
        if phase != "action":
            return self._closed("actions")
        if not seat["hands"]:
            return 400, {"error": f"@{user} is not in this hand"}
        hi, err = self._hand_arg(seat, params.get("hand"))
        if err:
            return 400, {"error": err}
        hand = seat["hands"][hi]
        if hand["status"] != "active":
            return 400, {"error": f"hand {hi + 1} is finished ({hand['status']})"}
        if hand["act"]:
            return 409, {"error": "already chosen this round", "action": hand["act"], "hand": hi + 1}
        nhands = len(seat["hands"]) + sum(1 for h in seat["hands"] if h["act"] == "split")
        cost = 0
        if action == "hit" and not can_hit(hand, rules):
            return 400, {"error": "split aces take one card each - stand"}
        if action == "double":
            if not can_double(hand, rules):
                return 400, {"error": "can't double this hand"}
            cost = hand["bet"]
        elif action == "split":
            if not can_split(hand, nhands, rules):
                return 400, {"error": "can't split this hand"}
            cost = hand["base"]
        elif action == "surrender" and not can_surrender(hand, nhands, rules):
            return 400, {"error": "can't surrender this hand"}
        err = self._afford(params, cost, self.cfg["currency"]) if cost else None
        if err:
            return 400, {"error": err}
        events = []
        if action == "double":
            hand["bet"] += cost
            hand["doubled"] = True
            events.append(_ev("debit", user, cost, "double", self._bid(si, tail=f"/h{hi + 1}"),
                              f"Double · seat {si + 1}", g["id"]))
        elif action == "split":
            hand["pending"] = cost
            events.append(_ev("debit", user, cost, "split", self._bid(si, tail=f"/h{hi + 1}"),
                              f"Split · seat {si + 1}", g["id"]))
        hand["act"] = action
        logged = self._persist(events) if events else []
        self._maybe_close_window()
        return 200, {"action": action, "seat": si + 1, "hand": hi + 1, "cost": cost, "debits": _debits(logged),
                     "player": self._player(user)}

    def _insurance(self, si: int, seat: dict, action: str, params: dict) -> tuple[int, dict]:
        g = self.g
        if g["phase"] != "insurance":
            return self._closed("insurance")
        user = seat["user"]
        if not seat["hands"]:
            return 400, {"error": f"@{user} is not in this hand"}
        if seat["ins_state"]:
            return 409, {"error": "already decided", "insurance": seat["ins_state"]}
        events = []
        cost = seat["hands"][0]["bet"] // 2
        if action == "decline":
            seat["ins_state"] = "declined"
        else:
            if cost < 1:
                return 400, {"error": "the bet is too small to insure (insurance is half the bet: bet at least 2)"}
            err = self._afford(params, cost, self.cfg["currency"])
            if err:
                return 400, {"error": err}
            seat["ins"], seat["ins_state"] = cost, "taken"
            events.append(_ev("debit", user, cost, "insurance", self._bid(si, tail="/ins"),
                              f"Insurance · seat {si + 1}", g["id"]))
        logged = self._persist(events) if events else []
        self._maybe_close_window()
        return 200, {"action": action, "seat": si + 1, "cost": cost if action != "decline" else 0,
                     "debits": _debits(logged), "player": self._player(user)}

    def _maybe_close_window(self) -> None:
        """Everybody who can choose has chosen: no reason to wait out the clock."""
        g = self.g
        if g["phase"] == "action":
            done = all(h["act"] or s["leaving"] for s in g["seats"] if s for h in s["hands"] if h["status"] == "active")
            if done:
                self._shorten(0.8)
        elif g["phase"] == "insurance":
            done = all(s["ins_state"] or s["leaving"] or s["hands"][0]["bet"] < 2 for s in g["seats"] if s and s["hands"])
            if done:
                self._shorten(0.8)

    # ---- the game ---------------------------------------------------------------------------------

    def advance(self) -> None:
        ph = self.g["phase"]
        if ph == "betting":
            self._close_betting()
        elif ph == "dealing":
            self._after_deal()
        elif ph == "insurance":
            self._after_insurance()
        elif ph == "action":
            self._resolve_round()
        elif ph == "resolve":
            self._after_resolve()
        elif ph == "dealer":
            self._settle()
        elif ph == "settle":
            self._after_settle()
        else:
            self._to_idle()

    def _open_betting(self, seconds: float, key: str) -> None:
        g = self.g
        g["round"] = 0
        g["deal"] = None
        self._phase("betting", seconds)
        self._say(key)
        self.save()

    def _close_betting(self) -> None:
        g, cfg = self.g, self.cfg
        for i, s in enumerate(g["seats"]):
            if s and s["bet"] <= 0:
                g["seats"][i] = None                   # not betting again = the seat is free
        self._promote_queue()
        players = [i for i, s in enumerate(g["seats"]) if s and s["bet"] > 0]
        if not players:
            g["idle"] += 1
            if g["closing"] or g["idle"] >= cfg["idle_windows"]:
                self._finish("idle" if g["hand_no"] else "no_bets")
            else:
                self._open_betting(float(cfg["bet_seconds"]), "next" if g["hand_no"] else "open")
            return
        g["idle"] = 0
        self._deal(players)

    def _new_shoe(self, players: int) -> None:
        cfg = self.cfg
        table = parse_deck_table(cfg["deck_table"]) or parse_deck_table(DEFAULT_DECK_TABLE)
        self.shoe = Shoe(decks_for(table, players), cfg["penetration_pct"])
        self.g["tally"]["shoes"] += 1
        self.g["shuffled"] = True

    def _draw(self) -> str:
        """The next card. A shoe that runs dry in the middle of a hand is rebuilt from every card that is
        not on the felt (and reshuffled again before the next hand)."""
        sh = self.shoe
        if sh.left <= 0:
            fresh = Shoe(sh.decks, 100, exclude=self._in_play())
            fresh.spent = True
            self.shoe = sh = fresh
            if sh.left <= 0:                          # (every card is on the felt: a freak, not a crash)
                self.shoe = sh = Shoe(sh.decks, 100)
        return sh.draw()

    def _deal(self, players: list[int]) -> None:
        g, cfg = self.g, self.cfg
        g["hand_no"] += 1
        g["round"] = 0
        g["settled"] = False
        g["rules"] = rules_of(cfg)                       # settings apply from this hand on, never mid-hand
        g["shuffled"] = False
        sh = self.shoe
        if sh is None or sh.cut_passed or sh.left < len(players) * 5 + 6:
            self._new_shoe(len(players))
        for i in players:
            s = g["seats"][i]
            s["hands"] = [new_hand(self._next_uid(), [], s["bet"])]
            s["ins"], s["ins_state"], s["ins_paid"] = 0, None, False
            g["last_bets"][s["user"]] = s["bet"]
        while len(g["last_bets"]) > ROUND_PLAYERS_MAX:
            g["last_bets"].pop(next(iter(g["last_bets"])))
        dealer = {"cards": [], "reveal": False, "bj": False, "peeked": None}
        order: list[list[int]] = []
        for k in range(2):
            for i in players:
                h = g["seats"][i]["hands"][0]
                h["cards"].append(self._draw())
                order.append([i + 1, 0, k])
            dealer["cards"].append(self._draw())
            order.append([0, 0, k])
        g["dealer"] = dealer
        dealer["bj"] = is_blackjack(dealer["cards"])
        for i in players:
            h = g["seats"][i]["hands"][0]
            if is_blackjack(h["cards"]):
                h["status"] = "blackjack"
        step = int(cfg["card_ms"])
        shuffle = SHUFFLE_MS if g["shuffled"] else 0
        t0 = DEAL_LEAD_MS + shuffle
        g["deal"] = {"kind": "deal", "t0": t0, "step": step, "order": order, "moves": [], "flip": None, "shuffle": shuffle}
        self._phase("dealing", (t0 + len(order) * step + DEAL_TAIL_MS) / 1000.0)
        self._say("shuffle" if g["shuffled"] else "deal")
        self._clip("deal_clip")
        self.save()

    def _after_deal(self) -> None:
        g, cfg = self.g, self.cfg
        up = g["dealer"]["cards"][0]
        if g["rules"]["insurance"] and up[0] == "A" and any(
                s and s["hands"] and s["hands"][0]["bet"] >= 2 for s in g["seats"]):
            g["deal"] = None
            self._phase("insurance", float(cfg["insurance_seconds"]))
            self._say("insurance")
            self.save()
            return
        self._peek_or_play()

    def _after_insurance(self) -> None:
        self._peek_or_play()

    def _peek_or_play(self) -> None:
        g = self.g
        d = g["dealer"]
        up = d["cards"][0]
        g["deal"] = None
        if g["rules"]["dealer_peeks"] and card_points(up) in (1, 10):
            d["peeked"] = True
            if d["bj"]:
                self._say("peek_bj")
                self._dealer_phase()
                return
            self._start_action(peeked=True)
            return
        self._start_action()

    def _start_action(self, peeked: bool = False) -> None:
        g, cfg = self.g, self.cfg
        if not any(h["status"] == "active" for s in g["seats"] if s for h in s["hands"]):
            self._dealer_phase()
            return
        g["round"] += 1
        g["deal"] = None
        secs = float(cfg["action_seconds"] if g["round"] == 1 else cfg["later_action_seconds"])
        self._phase("action", secs)
        self._say("peek_ok" if peeked else ("action" if g["round"] == 1 else "more"))
        self.save()

    def _resolve_round(self) -> None:
        """The action window is over: every hand's choice (silence = stand) is carried out, in seat
        order, and the cards it needs come off the shoe."""
        g, cfg = self.g, self.cfg
        rules = g["rules"]
        order: list[list[int]] = []
        moves: list[list[int]] = []
        for si, seat in enumerate(g["seats"]):
            if not seat or not seat["hands"]:
                continue
            j = 0
            while j < len(seat["hands"]):
                hand = seat["hands"][j]
                if hand["status"] != "active":
                    j += 1
                    continue
                act, hand["act"] = hand["act"] or "stand", None
                if act == "stand":
                    hand["status"] = "stand"
                elif act == "surrender":
                    hand["status"] = "surrender"
                elif act in ("hit", "double"):
                    hand["cards"].append(self._draw())
                    order.append([si + 1, j, len(hand["cards"]) - 1])
                    total = hand_value(hand["cards"])[0]
                    if total > 21:
                        hand["status"] = "bust"
                    elif act == "double":
                        hand["status"] = "stand"
                    elif rules["five_card_charlie"] and len(hand["cards"]) >= 5:
                        hand["status"] = "charlie"
                    elif total == 21:
                        hand["status"] = "stand"
                elif act == "split":
                    second = hand["cards"].pop()
                    new = new_hand(self._next_uid(), [second], hand["pending"], split=True)
                    hand["pending"] = 0
                    hand["split"] = True
                    hand["aces"] = new["aces"] = second[0] == "A"
                    seat["hands"].insert(j + 1, new)
                    moves.append([si + 1, j, j + 1])
                    for k in (j, j + 1):
                        h = seat["hands"][k]
                        h["cards"].append(self._draw())
                        order.append([si + 1, k, len(h["cards"]) - 1])
                        total = hand_value(h["cards"])[0]
                        pair = card_points(h["cards"][0]) == card_points(h["cards"][1])
                        if h["aces"] and rules["split_aces_one_card"]:
                            # one card each - unless the ace pairs up again and aces may be resplit
                            again = rules["resplit_aces"] and pair and len(seat["hands"]) < rules["max_hands"]
                            h["status"] = "active" if again else "stand"
                        elif total == 21:
                            h["status"] = "stand"
                    j += 1                                    # (the new hand is already dealt)
                j += 1
        if not order and not moves:
            self._after_resolve()
            return
        step = max(110, int(cfg["card_ms"] * 0.85))
        g["deal"] = {"kind": "hit", "t0": 300, "step": step, "order": order, "moves": moves, "flip": None, "shuffle": 0}
        self._phase("resolve", (300 + len(order) * step + 500) / 1000.0)
        self.save()

    def _after_resolve(self) -> None:
        g, cfg = self.g, self.cfg
        live = any(h["status"] == "active" for s in g["seats"] if s for h in s["hands"])
        if live and g["round"] < cfg["max_rounds"]:
            self._start_action()
            return
        for s in g["seats"]:
            if s:
                for h in s["hands"]:
                    if h["status"] == "active":
                        h["status"], h["act"] = "stand", None
        self._dealer_phase()

    def _dealer_phase(self) -> None:
        g, cfg = self.g, self.cfg
        d, rules = g["dealer"], g["rules"]
        d["reveal"] = True
        order: list[list[int]] = []
        need = not d["bj"] and any(h["status"] == "stand" for s in g["seats"] if s for h in s["hands"])
        while need:
            total, soft = hand_value(d["cards"])
            if total < 17 or (total == 17 and soft and rules["dealer_hits_soft_17"]):
                d["cards"].append(self._draw())
                order.append([0, 0, len(d["cards"]) - 1])
            else:
                break
        step = max(450, int(cfg["card_ms"] * 3))
        g["deal"] = {"kind": "dealer", "t0": 650, "step": step, "order": order, "moves": [], "flip": 1, "shuffle": 0}
        self._phase("dealer", (650 + len(order) * step + 750) / 1000.0)
        if not d["bj"]:
            self._say("dealer")
        self.save()

    def _settle(self) -> None:
        g, cfg = self.g, self.cfg
        d, rules = g["dealer"], g["rules"]
        total, soft = hand_value(d["cards"])
        dealer = {"total": total, "bust": total > 21, "bj": d["bj"]}
        tally = g["tally"]
        events: list[dict] = []
        gid = g["id"]
        tally["hands"] += 1
        if d["bj"]:
            tally["dealer_blackjacks"] += 1
        if dealer["bust"]:
            tally["dealer_busts"] += 1
        results = []
        any_win = any_bj = False
        for si, seat in enumerate(g["seats"]):
            if not seat or not seat["hands"]:
                continue
            user = seat["user"]
            row = {"user": user, "seat": si + 1, "hands": [], "net": 0}
            lost_total = 0                                    # bets lost to a dealer blackjack that peeking didn't catch
            for j, h in enumerate(seat["hands"]):
                if h["paid"]:
                    continue
                outcome, pay = settle_hand(h, dealer, rules)
                h["result"] = {"outcome": outcome, "pay": pay, "net": pay - h["bet"]}
                h["paid"] = True
                tally["player_hands"] += 1
                if h["doubled"]:
                    tally["doubles"] += 1
                if outcome == "blackjack":
                    tally["blackjacks"] += 1
                    any_bj = True
                tally["busts" if outcome == "bust" else "wins" if outcome in ("win", "blackjack", "charlie")
                      else "pushes" if outcome in ("push", "surrender") else "losses"] += 1
                any_win = any_win or outcome in ("win", "blackjack", "charlie")
                if pay > 0:
                    reason = "push" if outcome == "push" else "surrender" if outcome == "surrender" else "win"
                    label = {"blackjack": f"Blackjack {rules['blackjack_pays']}", "win": f"Win · {self._vs(h, total, dealer)}",
                             "charlie": "Five-card Charlie", "push": f"Push · {self._vs(h, total, dealer)}",
                             "surrender": "Surrender"}[outcome]
                    events.append(_ev("credit", user, pay, reason, self._bid(si, tail=f"/h{j + 1}"),
                                      f"{label} · seat {si + 1}", gid))
                elif outcome == "lose" and d["bj"]:
                    lost_total += h["bet"]
                row["hands"].append({"outcome": outcome, "bet": h["bet"], "pay": pay})
                row["net"] += pay - h["bet"]
            if d["bj"] and not rules["dealer_peeks"] and lost_total > seat["bet"]:
                # no peek: a dealer blackjack takes the ORIGINAL bet only - doubles and splits come back
                back = lost_total - seat["bet"]
                events.append(_ev("credit", user, back, "refund", self._bid(si, tail="/obo"),
                                  f"Dealer blackjack - original bet only · seat {si + 1}", gid))
                row["net"] += back
                row["hands"][0]["pay"] += back if row["hands"] else 0
            if seat["ins"] and not seat["ins_paid"]:
                seat["ins_paid"] = True
                tally["insurance"] += 1
                if d["bj"]:
                    events.append(_ev("credit", user, seat["ins"] * 3, "insurance", self._bid(si, tail="/ins"),
                                      f"Insurance 2:1 · seat {si + 1}", gid))
                    row["net"] += seat["ins"] * 2
                else:
                    row["net"] -= seat["ins"]
                row["insurance"] = {"bet": seat["ins"], "won": bool(d["bj"])}
            results.append(row)
        g["settled"] = True
        g["last"] = {"hand_no": g["hand_no"], "dealer": {"cards": list(d["cards"]), "total": total, "bust": dealer["bust"],
                                                           "bj": d["bj"]}, "results": results,
                     "net": sum(r["net"] for r in results)}
        secs = float(cfg["settle_seconds"])
        self._phase("settle", secs)
        self._say("bust" if dealer["bust"] else "house" if sum(r["net"] for r in results) < 0 else "players")
        if events:
            self._persist(events)
        else:
            self.save()
        self._clip("blackjack_clip" if any_bj or d["bj"] else "win_clip" if any_win else "deal_clip")

    @staticmethod
    def _vs(h: dict, dealer_total: int, dealer: dict) -> str:
        t = hand_value(h["cards"])[0]
        return f"{t} vs dealer bust" if dealer["bust"] else f"{t} vs {dealer_total}"

    def _after_settle(self) -> None:
        g = self.g
        g["settled"] = False
        for i, s in enumerate(g["seats"]):
            if not s:
                continue
            if s["leaving"]:
                g["seats"][i] = None
                continue
            s["hands"], s["bet"], s["ins"], s["ins_state"], s["ins_paid"] = [], 0, 0, None, False
        g["dealer"] = None
        self._promote_queue()
        if g["closing"]:
            self._finish("closed")
            return
        self._open_betting(float(self.cfg["bet_seconds"]), "next")

    def _finish(self, outcome: str, events: list[dict] | None = None) -> None:
        g = self.g
        events = (events or []) + self.open_settlement()
        if events:
            self._persist(events)
        g["outcome"] = outcome
        g["summary"] = self.summary(outcome)
        self._record_game(g["summary"])
        secs = float(self.cfg["summary_seconds"])
        self._phase("over", min(secs, 5.0) if outcome in ("no_bets", "idle") else secs)
        self._say("closed")
        self.save()

    def summary(self, outcome: str) -> dict:
        g = self.g
        return {"outcome": outcome, "text": _OUTCOMES.get(outcome, outcome), "hands": g["hand_no"],
                "tally": dict(g["tally"]), "total_bet": g["debits"], "total_paid": g["credits"],
                "house_net": g["debits"] - g["credits"], "players": self.players_summary(),
                "test": bool(g.get("test")), "currency": g["currency"]}

    def record(self, s: dict) -> None:
        st = self._st
        st["games"] += 1
        t = s.get("tally") or {}
        for k in self.TALLY_KEYS:
            st[k] += int(t.get(k, 0))
        if s["outcome"] == "no_bets":
            st["no_bets"] += 1
        elif s["outcome"] in ("stopped", "restart", "error"):
            st["stopped"] += 1
        st["total_bet"] += s["total_bet"]
        st["total_paid"] += s["total_paid"]
        st["house_net"] += s["house_net"]

    # ---- views ----------------------------------------------------------------------------------------

    def _hand_view(self, h: dict) -> dict:
        total, soft = hand_value(h["cards"])
        return {"uid": h["uid"], "cards": list(h["cards"]), "bet": h["bet"], "pending": h["pending"], "total": total,
                "soft": soft, "status": h["status"], "doubled": h["doubled"], "split": h["split"], "act": h["act"],
                "result": h["result"]}

    def _seat_view(self, i: int, seat: dict | None, phase: str) -> dict:
        n = i + 1
        if not seat:
            return {"n": n, "user": None, "state": "empty"}
        if seat["hands"]:
            state = "done" if phase in ("settle", "over") else "playing"
        else:
            state = "bet" if seat["bet"] > 0 else "held"
        return {"n": n, "user": seat["user"], "state": state, "bet": seat["bet"],
                "stake": self._on_line(seat), "ins": seat["ins"], "ins_state": seat["ins_state"],
                "leaving": seat["leaving"], "last_bet": self.g["last_bets"].get(seat["user"], 0),
                "hands": [self._hand_view(h) for h in seat["hands"]]}

    def _dealer_view(self) -> dict:
        g = self.g
        d = g["dealer"]
        name = self.cfg["dealer_name"]
        if not d:
            return {"name": name, "cards": [], "total": 0, "soft": False, "hidden": False, "bj": None, "bust": False}
        cards = list(d["cards"])
        shown = cards
        if not d["reveal"] and len(cards) >= 2:
            shown = [cards[0], None] + [None] * (len(cards) - 2)
        vis = [c for c in shown if c]
        total, soft = hand_value(vis)
        hidden = any(c is None for c in shown)
        return {"name": name, "cards": shown, "total": total, "soft": soft and not hidden, "hidden": hidden,
                "bj": (d["bj"] if d["reveal"] or d["peeked"] else None), "bust": total > 21,
                "peeked": d["peeked"], "up": cards[0] if cards else None}

    def shoe_view(self) -> dict:
        sh = self.shoe
        g = self.g
        cfg = self.cfg
        table = parse_deck_table(cfg["deck_table"]) or parse_deck_table(DEFAULT_DECK_TABLE)
        players = self._players_now() if g is not None else 0
        nxt = decks_for(table, max(1, players))
        if sh is None:
            return {"decks": 0, "size": 0, "left": 0, "dealt": 0, "discards": 0, "cut_at": 0, "to_cut": 0,
                    "cut_passed": False, "next_decks": nxt, "shuffles": (g or {}).get("tally", {}).get("shoes", 0),
                    "penetration_pct": cfg["penetration_pct"], "shuffled": False}
        in_play = len(self._in_play())
        return {"decks": sh.decks, "size": sh.size, "left": sh.left, "dealt": sh.pos,
                "discards": max(0, sh.pos - in_play), "cut_at": sh.cut, "to_cut": max(0, sh.cut - sh.pos),
                "cut_passed": sh.cut_passed, "next_decks": nxt, "shuffles": g["tally"]["shoes"] if g else 0,
                "penetration_pct": cfg["penetration_pct"], "shuffled": bool(g and g.get("shuffled"))}

    def rules_view(self, rules: dict | None = None, decks: int | None = None) -> dict:
        rules = rules or rules_of(self.cfg)
        table = parse_deck_table(self.cfg["deck_table"]) or parse_deck_table(DEFAULT_DECK_TABLE)
        return {**rules, "text": rules_text(rules), "edge_pct": round(house_edge(rules, decks), 3),
                "deck_table": deck_table_view(table)}

    def _player(self, user: str) -> dict | None:
        g = self.g
        if g is None:
            return None
        si, qi = self._seat_index(user), self._queue_index(user)
        if si is not None:
            s = g["seats"][si]
            return {"user": user, "seat": si + 1, "queued": False, "stake": self._on_line(s), "bet": s["bet"],
                    "hands": [self._hand_view(h) for h in s["hands"]], "leaving": s["leaving"],
                    "last_bet": g["last_bets"].get(user, 0)}
        if qi is not None:
            return {"user": user, "seat": None, "queued": True, "position": qi + 1, "stake": g["queue"][qi]["bet"],
                    "bet": g["queue"][qi]["bet"], "hands": [], "leaving": False, "last_bet": g["last_bets"].get(user, 0)}
        return None

    def players_view(self) -> list[dict]:
        g = self.g
        rows = []
        tot = g.get("totals") or {}
        for i, s in enumerate(g["seats"]):
            if not s:
                continue
            t = tot.get(s["user"]) or {"bet": 0, "paid": 0}
            hands = [self._hand_view(h) for h in s["hands"]]
            status = ", ".join(f"{h['total']}{' soft' if h['soft'] else ''}" + (f" {h['status']}" if h["status"] not in ("active", "stand") else "")
                               for h in hands) if hands else ("ante up" if s["bet"] == 0 else "ready")
            rows.append({"user": s["user"], "seat": i + 1, "stake": self._on_line(s), "bet": s["bet"], "status": status,
                         "hands": len(hands), "net": t["paid"] - t["bet"], "queued": False})
        for k, e in enumerate(g["queue"]):
            t = tot.get(e["user"]) or {"bet": 0, "paid": 0}
            rows.append({"user": e["user"], "seat": None, "stake": e["bet"], "bet": e["bet"], "status": f"waiting #{k + 1}",
                         "hands": 0, "net": t["paid"] - t["bet"], "queued": True})
        return rows

    def table_bets(self) -> list[dict]:
        g = self.g
        if g is None:
            return []
        out = [{"user": s["user"], "amount": self._on_line(s)} for s in g["seats"] if s and self._on_line(s) > 0]
        out += [{"user": e["user"], "amount": e["bet"]} for e in g["queue"]]
        return out

    def game_view(self, now: float) -> dict:
        g, cfg = self.g, self.cfg
        t = self.timing(now)
        if t["ends_in_ms"] is not None and g.get("len"):
            t["phase_ms"] = max(g["len"], t["ends_in_ms"])
        phase = g["phase"]
        deal = g.get("deal") if phase in ("dealing", "resolve", "dealer") else None
        say = g.get("say") if g.get("say") and g["say"]["phase"] == phase else None
        rules = g["rules"]
        shoe = self.shoe_view()
        players = self.players_view()
        return {"id": g["id"], "test": g["test"], "phase": phase, **t, "hand_no": g["hand_no"], "round": g["round"],
                "rounds_max": cfg["max_rounds"], "closing": g["closing"], "seat_count": g["seat_count"],
                "seats": [self._seat_view(i, s, phase) for i, s in enumerate(g["seats"])],
                "queue": [{"user": e["user"], "bet": e["bet"]} for e in g["queue"]],
                "dealer": self._dealer_view(), "deal": deal, "shoe": shoe, "say": say,
                "rules": {**rules, "text": rules_text(rules), "edge_pct": round(house_edge(rules, shoe["decks"] or shoe["next_decks"]), 3)},
                "players": players, "at_risk": sum(p["stake"] for p in players), "last": g.get("last"),
                "outcome": g.get("outcome"), "summary": g.get("summary"), "currency": g["currency"],
                "min_bet": cfg["min_bet"], "max_bet": cfg["max_bet"], "commands_text": cfg["commands_text"],
                "dealer_name": cfg["dealer_name"], "seats_free": sum(1 for s in g["seats"] if s is None)}

    def idle_view(self) -> dict:
        cfg = self.cfg
        rules = rules_of(cfg)
        table = parse_deck_table(cfg["deck_table"]) or parse_deck_table(DEFAULT_DECK_TABLE)
        return {"currency": cfg["currency"], "seats": cfg["seats"], "dealer_name": cfg["dealer_name"],
                "rules": {**rules, "text": rules_text(rules), "edge_pct": round(house_edge(rules, table[0][1]), 3),
                          "deck_table": deck_table_view(table)},
                "min_bet": cfg["min_bet"], "max_bet": cfg["max_bet"], "commands_text": cfg["commands_text"]}

    def user_view(self, name: Any) -> dict:
        user = _clean_user(name)
        g = self.g
        player = self._player(user) if g is not None and user else None
        out: dict[str, Any] = {"user": user, "player": player, "session": self.ledger.session(user, self.key)}
        if player and g["phase"] == "action" and not player["queued"]:
            seat = g["seats"][self._seat_index(user)]
            rules = g["rules"]
            n = len(seat["hands"])
            out["can"] = [{"hand": i + 1, "chosen": h["act"], "hit": can_hit(h, rules) and not h["act"],
                           "stand": h["status"] == "active" and not h["act"],
                           "double": h["bet"] if can_double(h, rules) and not h["act"] else 0,
                           "split": h["base"] if can_split(h, n, rules) and not h["act"] else 0,
                           "surrender": can_surrender(h, n, rules) and not h["act"]}
                          for i, h in enumerate(seat["hands"]) if h["status"] == "active"]
        if player and g["phase"] == "insurance" and not player["queued"]:
            seat = g["seats"][self._seat_index(user)]
            out["insurance"] = {"cost": seat["hands"][0]["bet"] // 2 if seat["hands"] else 0, "state": seat["ins_state"]}
        return out

    def bets_payload(self) -> dict:
        cfg = self.cfg
        rules = rules_of(cfg)
        table = parse_deck_table(cfg["deck_table"]) or parse_deck_table(DEFAULT_DECK_TABLE)
        decks = sorted({d for _, d in table})
        return {"ok": True, "game": self.key, "currency": cfg["currency"], "min_bet": cfg["min_bet"],
                "max_bet": cfg["max_bet"], "seats": cfg["seats"], "queue_max": cfg["queue_max"],
                "rules": {**rules, "text": rules_text(rules)}, "house_edge_pct": round(house_edge(rules, None), 3),
                "edge_by_decks": {str(d): round(house_edge(rules, d), 3) for d in decks},
                "shoe": {"deck_table": deck_table_view(table), "penetration_pct": cfg["penetration_pct"]},
                "payouts": {"win": "1:1", "blackjack": cfg["blackjack_pays"], "insurance": "2:1",
                            "surrender": "1:2 back", "charlie": "1:1"},
                "actions": {"hit": ["hit", "h"], "stand": ["stand", "s", "stay"], "double": ["double", "dd", "d"],
                            "split": ["split", "sp"], "surrender": ["surrender", "sur"],
                            "insurance": ["insurance", "ins", "even"], "decline": ["decline", "noins", "no"]},
                "cards": "two characters: rank A 2-9 T J Q K + suit S H D C (TH = ten of hearts)",
                "rounding": "winnings are rounded down to whole coins (a blackjack on an odd bet, a surrender)",
                "text": [
                    f"Up to {cfg['seats']} seats around the felt; extra players wait in a queue of {cfg['queue_max']}. "
                    "Bet to take a seat (first free one, or `seat`); bet again every hand to keep it.",
                    "Everybody acts at the same time: each action round is a window in which every hand sends ONE "
                    "choice (hit, stand, double, split, surrender); the server then resolves them in seat order. "
                    "No choice by the end of the window = stand. Rounds repeat until every hand is finished.",
                    "Every bet is against the bank: /bet, doubles, splits and insurance are ledger debits; "
                    "payouts, pushes, surrenders and refunds are credits.",
                    f"The shoe is {', '.join(f'{d}' for d in decks)} decks by how many players are seated "
                    "and is reshuffled at the cut card."] + rules_text(rules)}

    def validate(self, params: dict) -> dict:
        """Dry run of a bet: ?user=&amount=&seat= (nothing moves)."""
        g, cfg = self.g, self.cfg
        out: dict[str, Any] = {"valid": False, "phase": self.phase}
        if g is None or g["phase"] != "betting":
            out["error"] = "bets are closed" if g else "no game running"
            return out
        user = _clean_user(params.get("user"))
        amt, err = _parse_coins(params.get("amount")) if params.get("amount") not in (None, "") else (None, None)
        want, serr = self._parse_seat(params.get("seat"))
        err = err or serr
        if not err and amt is not None:
            si, qi = self._seat_index(user) if user else None, self._queue_index(user) if user else None
            cur = g["seats"][si]["bet"] if si is not None else g["queue"][qi]["bet"] if qi is not None else 0
            err = self._stake_check(cur, amt)
        seat = None
        if not err and user and self._seat_index(user) is None:
            free = self._free_seat(want)
            if free is not None:
                seat = free + 1
            elif want is not None and g["seats"][want] is not None:
                err = f"seat {want + 1} is taken"
            elif len(g["queue"]) >= cfg["queue_max"]:
                err = "the table is full"
        out.update(valid=err is None, amount=amt, seat=seat, error=err)
        return out


BLACKJACK = Blackjack()           # restores a running table from games_blackjack.json (settled by the plugin)


# --------------------------------------------------------------------------
# what the Games pages need to load this game (see core.register_game)
# --------------------------------------------------------------------------

OVERLAY = {
    "script": "blackjack.js",
    "stateful": True,
    "appearance": list(Blackjack.APPEARANCE),
    # mirrors the defaults of the game's config section, for a renderer that runs before its
    # first config message arrives
    "defaults": {k: v for k, v in Blackjack.DEFAULTS.items()},
}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/games", tags=["games"])


@router.api_route("/api/blackjack/start", methods=["GET", "POST"])
async def api_bj_start(request: Request):
    return await _round_request(BLACKJACK, request, BLACKJACK.start_game)


@router.api_route("/api/blackjack/bet", methods=["GET", "POST"])
async def api_bj_bet(request: Request):
    return await _round_request(BLACKJACK, request, BLACKJACK.place)


@router.api_route("/api/blackjack/rebet", methods=["GET", "POST"])
@router.api_route("/api/blackjack/ditto", methods=["GET", "POST"])
async def api_bj_rebet(request: Request):
    return await _round_request(BLACKJACK, request, BLACKJACK.rebet)


@router.api_route("/api/blackjack/remove", methods=["GET", "POST"])
async def api_bj_remove(request: Request):
    return await _round_request(BLACKJACK, request, BLACKJACK.remove)


@router.api_route("/api/blackjack/leave", methods=["GET", "POST"])
@router.api_route("/api/blackjack/cashout", methods=["GET", "POST"])
async def api_bj_leave(request: Request):
    return await _round_request(BLACKJACK, request, BLACKJACK.leave)


@router.api_route("/api/blackjack/action", methods=["GET", "POST"])
async def api_bj_action(request: Request):
    return await _round_request(BLACKJACK, request, BLACKJACK.act)


def _shortcut(name: str):
    async def handler(request: Request):
        return await _round_request(BLACKJACK, request, lambda p: BLACKJACK.act({**p, "action": name}))
    handler.__name__ = f"api_bj_{name}"
    return handler


for _name in ("hit", "stand", "double", "split", "surrender", "insurance"):
    router.add_api_route(f"/api/blackjack/{_name}", _shortcut(_name), methods=["GET", "POST"])


@router.api_route("/api/blackjack/close", methods=["GET", "POST"])
async def api_bj_close(request: Request):
    return await _round_request(BLACKJACK, request, BLACKJACK.close)


@router.api_route("/api/blackjack/next", methods=["GET", "POST"])
async def api_bj_next(request: Request):
    return await _round_request(BLACKJACK, request, lambda _p: BLACKJACK.skip())


@router.get("/api/blackjack/table")
async def api_bj_table():
    return {"ok": True, **BLACKJACK.state_view()}


@router.get("/api/blackjack/seats")
async def api_bj_seats():
    st = BLACKJACK.state_view()
    g = st.get("game")
    if g is None:
        return {"ok": True, "state": st["state"], "seats": [], "queue": [], "idle": st["idle"]}
    return {"ok": True, "state": st["state"], "phase": g["phase"], "hand_no": g["hand_no"], "seats": g["seats"],
            "queue": g["queue"], "seats_free": g["seats_free"], "ends_in_ms": g["ends_in_ms"]}


@router.get("/api/blackjack/user/{name}")
async def api_bj_user(name: str):
    return {"ok": True, **BLACKJACK.user_view(name)}


@router.get("/api/blackjack/ledger")
async def api_bj_ledger(since: str | None = None, limit: str | None = None):
    return _ledger_reply(since, limit, "blackjack")


API_DOC = {
    "about":    "one table at a time: betting -> dealing -> [insurance] -> action rounds -> dealer -> settle -> "
                "betting ... Bets are against the bank; docs/blackjack.md",
    "start":    "GET|POST /games/api/blackjack/start   {seconds (first bet window), seats (1-14), test} -> "
                "{started, seats, state}; 409 while a table is open",
    "bet":      "GET|POST /games/api/blackjack/bet   {user, amount, seat?, balance?} -> {seat, amount, stake, queued, "
                "position, debits:[{user, amount, seq, reason}], player, state}; takes a seat (first free, or `seat`), "
                "else the queue; more on a stake adds to it; 409 bets_closed outside the betting window",
    "rebet":    "GET|POST /games/api/blackjack/rebet   (alias /ditto) {user} -> the same ante as last hand",
    "remove":   "GET|POST /games/api/blackjack/remove   {user, amount?} -> take back part (or all) of the stake before the "
                "deal (refund); the seat stays held",
    "leave":    "GET|POST /games/api/blackjack/leave   (alias /cashout) {user} -> before the deal: stake refunded, seat "
                "freed; during a hand: stands and leaves once it is settled",
    "action":   "GET|POST /games/api/blackjack/action   {user, action: hit|stand|double|split|surrender|insurance|decline, "
                "hand? (1-4), balance?} -> {action, seat, hand, cost, debits, player}; ONE choice per hand per round, "
                "final; double / split / insurance debit at once; 409 actions_closed / insurance_closed. Shortcuts: "
                "/hit /stand /double /split /surrender /insurance",
    "next":     "GET|POST /games/api/blackjack/next   end the current window / phase now",
    "close":    "GET|POST /games/api/blackjack/close   close the table after this hand",
    "table":    "GET /games/api/blackjack/table   the STATE (game: phase, seats, queue, dealer, shoe, rules, last)",
    "seats":    "GET /games/api/blackjack/seats   seats + queue",
    "user":     "GET /games/api/blackjack/user/{name}   the player's seat, hands, what they can do (can[]) + session totals",
    "ledger":   "GET /games/api/blackjack/ledger?since=0   reasons: bet, add, double, split, insurance (debits); win, push, "
                "surrender, insurance, refund (credits)",
    "stop":     "GET|POST /games/api/blackjack/stop   end the table now: every stake still on the line is returned",
    "bets":     "GET /games/api/blackjack/bets   the rules, payouts, house edge and the deck table",
    "preview":  "GET|POST /games/api/blackjack/preview   {overrides:{appearance}, seconds (2-60, default 8)} | "
                "x=&y=&scale= shorthand: the Edit Mode editor's Test in OBS - on screen for `seconds` with that "
                "look (STATE.preview), never touches the game; /preview/clear ends it",
}
