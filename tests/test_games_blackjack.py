"""Blackjack (games_blackjack): hand maths, the dealer, splits / doubles / insurance, the shoe, simultaneous
action rounds, settlement against the shared ledger, seats / queue / leave, and the HTTP API."""

import asyncio
import json
import secrets
import sys

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------------------------------------
# a table to play at: the real add-on from catalog/, driven directly (no clock: phases are advanced by hand)
# ---------------------------------------------------------------------------------------------------------

class Table:
    def __init__(self, world):
        self.world = world
        self.bj = sys.modules["hexcast_plugins.games_blackjack.blackjack"]
        self.core = sys.modules["hexcast_plugins.games.core"]
        self.G = self.bj.BLACKJACK

    # settings ------------------------------------------------------------------------------------------
    def set(self, **kw):
        cfg = dict(self.core.CONFIG["blackjack"])
        cfg.update(kw)
        self.core.CONFIG["blackjack"] = self.G.validate_config(cfg)

    def start(self, seats=6, **kw):
        kw.setdefault("seat_fill", "first")
        self.set(**kw)
        st, body = self.G.start_game({"seats": seats})
        assert st == 200, body
        return body

    # betting -------------------------------------------------------------------------------------------
    def bet(self, user, amount, **kw):
        return self.G.place({"user": user, "amount": amount, **kw})

    def bets(self, *pairs):
        for u, a in pairs:
            st, body = self.bet(u, a)
            assert st == 200, body

    def rig(self, cards):
        """Put these cards on top of a fresh two-deck shoe (deal order: seats left to right, dealer up, seats, dealer hole,
        then whatever the hits need)."""
        self.G.shoe = self.bj.Shoe(2)
        self.G.shoe.put_next(cards)

    def deal(self, cards):
        self.rig(cards)
        self.G.advance()                      # betting -> dealing
        assert self.G.phase == "dealing"
        self.G.advance()                      # dealing -> insurance / action / dealer
        return self.G.phase

    # acting --------------------------------------------------------------------------------------------
    def act(self, user, action, **kw):
        return self.G.act({"user": user, "action": action, **kw})

    def ok(self, user, action, **kw):
        st, body = self.act(user, action, **kw)
        assert st == 200, body
        return body

    def finish_hand(self):
        """Advance through whatever is left of the hand up to (and including) the settle phase."""
        for _ in range(40):
            if self.G.phase in ("settle", "over", "betting"):
                return
            self.G.advance()
        raise AssertionError("hand did not finish: " + self.G.phase)

    # looking -------------------------------------------------------------------------------------------
    def view(self):
        return self.G.state_view()["game"]

    def seat(self, user):
        for s in self.view()["seats"]:
            if s["user"] == user:
                return s
        return None

    def result(self, user, hand=0):
        return self.seat(user)["hands"][hand]["result"]

    def events(self, game="blackjack"):
        return [e for e in self.core.LEDGER.events if e.get("game") == game]

    def balance(self):
        """(debits, credits) of the blackjack ledger events."""
        ev = self.events()
        return (sum(e["amount"] for e in ev if e["type"] == "debit"), sum(e["amount"] for e in ev if e["type"] == "credit"))

    def player_net(self, user):
        ev = [e for e in self.events() if e["user"] == user]
        return sum(e["amount"] if e["type"] == "credit" else -e["amount"] for e in ev)


@pytest.fixture
def t(real_world):
    real_world.installer.install("games")
    real_world.installer.install("games_blackjack")
    real_world.host.load_all()
    assert "games_blackjack" in real_world.host.loaded, real_world.host.errors
    return Table(real_world)


def hv(bj, *cards):
    return bj.hand_value(list(cards))


def next_window(t):
    """Play the hand out (nobody acts: everybody stands) and wait in the next betting window."""
    t.finish_hand()
    for _ in range(20):
        if t.G.phase == "betting":
            return
        t.G.advance()
    raise AssertionError("no next betting window: " + t.G.phase)


# ---------------------------------------------------------------------------------------------------------
# hand valuation
# ---------------------------------------------------------------------------------------------------------

def test_hand_value_hard_soft_and_bust(t):
    bj = t.bj
    assert hv(bj, "KS", "7D") == (17, False)
    assert hv(bj, "AS", "6D") == (17, True)                     # soft 17
    assert hv(bj, "AS", "6D", "KC") == (17, False)              # the ace drops to 1: hard 17
    assert hv(bj, "AS", "AD") == (12, True)                     # one ace is 11, the other 1
    assert hv(bj, "AS", "AD", "9C") == (21, True)
    assert hv(bj, "AS", "AD", "AH", "AC") == (14, True)
    assert hv(bj, "AS", "AD", "AH", "AC", "TD") == (14, False)
    assert hv(bj, "KS", "QD", "5C") == (25, False)              # bust
    assert hv(bj, "2S", "3D", "4C", "5H") == (14, False)
    assert hv(bj, "AS", "2D", "3C") == (16, True)               # soft 16
    assert hv(bj, "TS", "JD") == (20, False) and hv(bj, "QS", "KD") == (20, False)   # all tens are 10


def test_blackjack_is_two_cards_only(t):
    bj = t.bj
    assert bj.is_blackjack(["AS", "KD"]) and bj.is_blackjack(["TH", "AC"]) and bj.is_blackjack(["JH", "AC"])
    assert not bj.is_blackjack(["7S", "7D", "7C"])              # three cards making 21
    assert not bj.is_blackjack(["AS", "9D"])


def test_blackjack_payout_rounds_down(t):
    bj = t.bj
    assert bj.bj_win(100, "3:2") == 150 and bj.bj_win(25, "3:2") == 37 and bj.bj_win(5, "3:2") == 7
    assert bj.bj_win(100, "6:5") == 120 and bj.bj_win(25, "6:5") == 30 and bj.bj_win(7, "6:5") == 8
    assert bj.bj_win(100, "1:1") == 100 and bj.bj_win(100, "2:1") == 200


def test_settle_hand_outcomes(t):
    bj = t.bj
    rules = bj.rules_of(bj.Blackjack.DEFAULTS)

    def hand(cards, status="stand", bet=100):
        return bj.new_hand(1, cards, bet, status=status)

    d = lambda total, bust=False, bj_=False: {"total": total, "bust": bust, "bj": bj_}
    assert bj.settle_hand(hand(["KS", "QD"]), d(19), rules) == ("win", 200)
    assert bj.settle_hand(hand(["KS", "9D"]), d(19), rules) == ("push", 100)
    assert bj.settle_hand(hand(["KS", "8D"]), d(19), rules) == ("lose", 0)
    assert bj.settle_hand(hand(["KS", "8D"]), d(26, bust=True), rules) == ("win", 200)
    assert bj.settle_hand(hand(["KS", "8D", "5C"], "bust"), d(26, bust=True), rules) == ("bust", 0)   # the player busts first
    assert bj.settle_hand(hand(["AS", "KD"], "blackjack"), d(19), rules) == ("blackjack", 250)
    assert bj.settle_hand(hand(["AS", "KD"], "blackjack"), d(21, bj_=True), rules) == ("push", 100)
    assert bj.settle_hand(hand(["KS", "QD", "AC"]), d(21, bj_=True), rules) == ("lose", 0)
    assert bj.settle_hand(hand(["KS", "QD"], "surrender"), d(19), rules) == ("surrender", 50)
    assert bj.settle_hand(hand(["2S", "3D", "2C", "4H", "5S"], "charlie"), d(20), rules) == ("charlie", 200)
    r65 = {**rules, "blackjack_pays": "6:5"}
    assert bj.settle_hand(hand(["AS", "KD"], "blackjack"), d(19), r65) == ("blackjack", 220)


# ---------------------------------------------------------------------------------------------------------
# the rules of acting
# ---------------------------------------------------------------------------------------------------------

def test_double_and_split_rules(t):
    bj = t.bj
    R = bj.rules_of(bj.Blackjack.DEFAULTS)

    def h(*cards, **kw):
        return bj.new_hand(1, list(cards), 100, **kw)

    assert bj.can_double(h("5S", "6D"), R) and bj.can_double(h("AS", "6D"), R)          # any two cards, soft too
    assert not bj.can_double(h("5S", "6D", "2C"), R)                                       # not on three cards
    assert not bj.can_double(h("5S", "6D", status="stand"), R)
    assert bj.can_double(h("5S", "6D", split=True), R)                                     # double after split
    assert not bj.can_double(h("5S", "6D", split=True), {**R, "double_after_split": False})
    assert not bj.can_double(h("AS", "6D", split=True, aces=True), R)                      # split aces: one card, no double
    assert bj.can_double(h("5S", "4D"), {**R, "double_on": "9-11"}) and not bj.can_double(h("5S", "3D"), {**R, "double_on": "9-11"})
    assert not bj.can_double(h("AS", "9D"), {**R, "double_on": "9-11"})                    # soft totals never double when limited
    assert bj.can_double(h("5S", "5D"), {**R, "double_on": "10-11"}) and not bj.can_double(h("5S", "4D"), {**R, "double_on": "10-11"})

    assert bj.can_split(h("8S", "8D"), 1, R) and bj.can_split(h("KS", "QD"), 1, R)         # equal values split (K + Q is a pair)
    assert not bj.can_split(h("8S", "9D"), 1, R) and not bj.can_split(h("8S", "8D", "2C"), 1, R)
    assert bj.can_split(h("8S", "8D"), 3, R) and not bj.can_split(h("8S", "8D"), 4, R)     # up to 4 hands
    assert not bj.can_split(h("8S", "8D"), 1, {**R, "max_hands": 1})
    assert bj.can_split(h("AS", "AD"), 1, R)
    assert not bj.can_split(h("AS", "AD", aces=True, split=True), 2, R)                    # no resplit aces
    assert bj.can_split(h("AS", "AD", aces=True, split=True), 2, {**R, "resplit_aces": True})
    assert not bj.can_hit(h("AS", "5D", aces=True, split=True), R)                         # split aces take one card
    assert bj.can_hit(h("AS", "5D", aces=True, split=True), {**R, "split_aces_one_card": False})

    assert not bj.can_surrender(h("KS", "6D"), 1, R)                                       # no surrender by default
    S = {**R, "surrender": True}
    assert bj.can_surrender(h("KS", "6D"), 1, S) and not bj.can_surrender(h("KS", "6D"), 2, S)
    assert not bj.can_surrender(h("KS", "6D", "2C"), 1, S) and not bj.can_surrender(h("KS", "6D", split=True), 1, S)
    assert not bj.can_surrender(h("KS", "6D"), 1, {**S, "dealer_peeks": False})            # late surrender needs the peek


# ---------------------------------------------------------------------------------------------------------
# the shoe: deck count by table size, cut card, shuffle
# ---------------------------------------------------------------------------------------------------------

def test_shoe_has_every_card_once_per_deck_and_uses_the_os_random_source(t):
    bj = t.bj
    sh = bj.Shoe(6, 75)
    assert sh.size == 312 and sh.left == 312 and sh.decks == 6
    for r in bj.RANKS:
        for s in bj.SUITS:
            assert sh.cards.count(r + s) == 6
    assert isinstance(bj._RNG, secrets.SystemRandom)
    assert sh.cards != sorted(sh.cards) and bj.Shoe(6).cards != bj.Shoe(6).cards          # shuffled, differently every time


def test_cut_card_sits_at_the_penetration(t):
    bj = t.bj
    sh = bj.Shoe(2, 75)
    assert sh.cut == 78 and not sh.cut_passed
    for _ in range(77):
        sh.draw()
    assert not sh.cut_passed
    sh.draw()
    assert sh.cut_passed and sh.left == 26
    assert bj.Shoe(8, 50).cut == 208 and bj.Shoe(1, 90).cut == 46


def test_deck_table_scales_with_the_players(t):
    bj = t.bj
    tbl = bj.parse_deck_table(bj.DEFAULT_DECK_TABLE)
    want = {1: 2, 2: 2, 3: 4, 4: 4, 5: 6, 6: 6, 7: 6, 8: 8, 9: 8, 14: 8}
    assert {n: bj.decks_for(tbl, n) for n in want} == want
    custom = bj.parse_deck_table("1:1, 4:6 ,10:8")
    assert [bj.decks_for(custom, n) for n in (1, 3, 4, 9, 10, 14)] == [1, 1, 6, 6, 8, 8]
    for bad in ("", "2:2", "1:9", "1:0", "1:2,1:4", "1:2,3:4,2:6", "abc", "1-2:2", "1:2,3", None, 7):
        assert bj.parse_deck_table(bad) is None, bad
    assert [r["players"] for r in bj.deck_table_view(tbl)] == ["1-2", "3-4", "5-7", "8+"]


def test_first_shoe_is_sized_by_the_seated_players(t):
    for players, decks in ((1, 2), (2, 2), (3, 4), (4, 4), (5, 6), (7, 6), (8, 8), (10, 8)):
        t.start(seats=10)
        for i in range(players):
            t.bets((f"p{i}", 10))
        t.G.advance()
        assert t.G.shoe.decks == decks, players
        assert t.view()["shoe"]["decks"] == decks
        t.G.stop()


def test_shoe_is_reshuffled_at_the_cut_card_and_resized_to_the_table(t):
    t.start(seats=10, bet_seconds=5)
    t.bets(("a", 10), ("b", 10))
    t.G.advance()
    assert t.G.shoe.decks == 2 and t.G.g["tally"]["shoes"] == 1
    first = t.G.shoe
    # the table grows to six players, but the shoe is not thrown away until the cut card is reached
    next_window(t)
    t.bets(("a", 10), ("b", 10), ("c", 10), ("d", 10), ("e", 10), ("f", 10))
    first.cut = first.size                                 # far from the cut card (and plenty of cards left)
    t.G.advance()
    assert t.G.shoe is first and first.decks == 2           # outgrown, but still the same shoe
    next_window(t)
    t.bets(("a", 10), ("b", 10), ("c", 10), ("d", 10), ("e", 10), ("f", 10))
    first.cut = 0                                            # the cut card has been reached
    t.G.advance()
    assert t.G.shoe is not first and t.G.shoe.decks == 6     # the bigger shoe, at the shuffle
    assert t.G.g["tally"]["shoes"] == 2
    assert t.view()["shoe"]["shuffled"] is True and t.view()["deal"]["shuffle"] > 0     # the shuffle is animated


def test_a_shoe_that_cannot_cover_the_hand_is_replaced_early(t):
    t.start(seats=10)
    t.bets(*[(f"p{i}", 10) for i in range(8)])
    t.G.shoe = t.bj.Shoe(2)
    t.G.shoe.pos = t.G.shoe.size - 20                       # 20 cards left, 8 players need ~46
    t.G.advance()
    assert t.G.shoe.decks == 8 and t.G.shoe.pos == 18        # new shoe, 8 players x 2 + dealer 2 dealt


def test_the_shoe_that_runs_dry_mid_hand_is_rebuilt_without_the_cards_on_the_felt(t):
    bj = t.bj
    t.start(seats=3)
    t.bets(("a", 10))
    t.G.advance()
    sh = t.G.shoe
    sh.pos = sh.size                                       # nothing left in the shoe
    on_felt = t.G._in_play()
    assert len(on_felt) == 4
    card = t.G._draw()
    assert t.G.shoe is not sh and t.G.shoe.spent and t.G.shoe.cut_passed        # rebuilt, reshuffled before the next hand
    assert t.G.shoe.size == 104 - 4 and t.G.shoe.pos == 1
    for c in {*bj.Shoe(2).cards}:                           # two decks in all: what is on the felt is not in the shoe any more
        assert t.G.shoe.cards.count(c) + on_felt.count(c) == 2, c


# ---------------------------------------------------------------------------------------------------------
# seats, the queue, leaving, staying
# ---------------------------------------------------------------------------------------------------------

def test_first_bets_take_seats_from_the_middle_out(t):
    bj = t.bj
    assert bj.seat_order(10, "center") == [5, 4, 6, 3, 7, 2, 8, 1, 9, 0]
    assert bj.seat_order(7, "center") == [3, 4, 2, 5, 1, 6, 0]
    assert bj.seat_order(3, "center") == [1, 2, 0]
    assert bj.seat_order(4, "first") == [0, 1, 2, 3]
    t.start(seats=10, seat_fill="center")
    seen = []
    for u in ("a", "b", "c"):
        st, body = t.bet(u, 10)
        assert st == 200
        seen.append(body["seat"])
    assert seen == [6, 5, 7]                                 # 1-based


def test_a_requested_seat_is_taken_if_free(t):
    t.start(seats=8)
    st, body = t.bet("ann", 10, seat=5)
    assert st == 200 and body["seat"] == 5
    st, body = t.bet("bob", 10, seat=5)
    assert st == 400 and "taken by @ann" in body["error"]
    assert t.bet("cy", 10, seat=9)[0] == 400 and t.bet("cy", 10, seat=0)[0] == 400 and t.bet("cy", 10, seat="x")[0] == 400
    assert t.bet("cy", 10, seat=1)[1]["seat"] == 1


def test_betting_more_adds_to_the_stake_and_respects_the_limits(t):
    t.start(seats=4, min_bet=10, max_bet=100)
    assert t.bet("ann", 5)[0] == 400                        # below the minimum
    st, body = t.bet("ann", 40)
    assert st == 200 and body["stake"] == 40 and body["debits"][0]["reason"] == "bet"
    st, body = t.bet("ann", 5)                              # a top-up needs no minimum
    assert st == 200 and body["stake"] == 45 and body["debits"][0]["reason"] == "add"
    st, body = t.bet("ann", 60)
    assert st == 400 and "max bet is 100" in body["error"] and "45 already down" in body["error"]
    assert t.bet("ann", 55)[1]["stake"] == 100
    for bad in (0, -5, "x", 2.5, None, True):
        assert t.bet("bob", bad)[0] == 400, bad
    assert t.bet("", 10)[0] == 400
    assert [e["amount"] for e in t.events()] == [40, 5, 55]


def test_balance_guard_refuses_what_the_player_cannot_cover(t):
    t.start(seats=4)
    st, body = t.bet("ann", 100, balance=50)
    assert st == 400 and "not enough hexcoins" in body["error"] and t.events() == []
    assert t.bet("ann", 100, balance=100)[0] == 200


def test_overflow_players_queue_and_are_seated_as_seats_free_up(t):
    t.start(seats=2, queue_max=3)
    t.bets(("a", 10), ("b", 20))
    st, body = t.bet("c", 30)
    assert st == 200 and body["queued"] and body["position"] == 1 and body["seat"] is None
    assert t.bet("d", 40)[1]["position"] == 2
    assert t.bet("c", 5)[1]["stake"] == 35                  # more on a queued stake adds to it
    assert [q["user"] for q in t.view()["queue"]] == ["c", "d"]
    assert t.view()["players"][-1]["status"] == "waiting #2"
    # a seat opens (a leaves): the first in the queue sits down with the stake they put up
    st, body = t.G.leave({"user": "a"})
    assert st == 200 and body["left"] and body["credits"] == [{"user": "a", "amount": 10}]
    assert t.seat("c")["bet"] == 35 and [q["user"] for q in t.view()["queue"]] == ["d"]
    assert t.player_net("c") == -35                          # (no coins moved when they were seated)
    t.bet("e", 5), t.bet("f", 5)                             # the queue holds queue_max
    assert t.bet("g", 5)[0] == 400
    # leaving the queue returns the stake
    st, body = t.G.leave({"user": "d"})
    assert body["credits"] == [{"user": "d", "amount": 40}] and t.G._queue_index("d") is None


def test_nobody_betting_again_loses_the_seat_and_the_queue_moves_up(t):
    t.start(seats=2, bet_seconds=5)
    t.bets(("a", 10), ("b", 10))
    t.bet("c", 10)                                           # queued
    t.deal(["5S", "6S", "9H", "5D", "6D", "7D"])           # a 10, b 12, dealer 16
    next_window(t)
    seat_b = t.seat("b")
    assert seat_b["state"] == "held" and seat_b["bet"] == 0 and seat_b["last_bet"] == 10        # held for this window
    t.bet("a", 10)                                           # a bets again, b does not
    t.G.advance()                                            # the window closes
    assert t.G.phase == "dealing"
    users = [s["user"] for s in t.view()["seats"]]
    assert "b" not in users and "a" in users and "c" in users          # b's seat went to the queue
    assert t.view()["queue"] == []


def test_rebet_repeats_the_last_ante_and_remove_takes_stake_back(t):
    t.start(seats=3, bet_seconds=5, min_bet=5)
    t.bet("ann", 40)
    t.deal(["TS", "9S", "8D", "7C"])                          # ann 18 vs dealer 16
    next_window(t)
    st, body = t.G.rebet({"user": "ann"})
    assert st == 200 and body["stake"] == 40 and body["debits"][0]["amount"] == 40
    assert t.G.rebet({"user": "ann"})[0] == 400              # already bet
    assert t.G.rebet({"user": "zed"})[0] == 400              # nothing to repeat
    st, body = t.G.remove({"user": "ann", "amount": 10})
    assert st == 200 and body["stake"] == 30 and body["credits"] == [{"user": "ann", "amount": 10}]
    assert t.G.remove({"user": "ann", "amount": 28})[0] == 400          # would leave 2 < min_bet
    assert t.G.remove({"user": "ann", "amount": 99})[0] == 400
    st, body = t.G.remove({"user": "ann"})
    assert body["stake"] == 0 and t.seat("ann")["state"] == "held"       # the seat stays held until the window closes


def test_leaving_before_the_deal_returns_the_stake_and_frees_the_seat(t):
    t.start(seats=4)
    t.bets(("ann", 50), ("bob", 70))
    st, body = t.G.leave({"user": "ann"})
    assert st == 200 and body["credits"] == [{"user": "ann", "amount": 50}]
    assert t.seat("ann") is None and t.player_net("ann") == 0
    assert t.G.leave({"user": "nobody"})[0] == 400


def test_leaving_in_the_middle_of_a_hand_stands_and_leaves_after_settling(t):
    t.start(seats=3)
    t.bets(("ann", 10), ("bob", 10))
    t.deal(["5S", "TH", "9D", "6S", "7C", "TC"])           # ann 5+6=11, bob T+7=17, dealer 9+T=19
    st, body = t.G.leave({"user": "ann"})
    assert st == 200 and body["left"] is False and body["leaving"] == "after this hand"
    t.ok("bob", "stand")
    assert t.G.phase == "action"
    t.finish_hand()                                          # ann never chose: she stands on 11 and loses
    assert t.result("ann")["outcome"] == "lose" and t.result("bob")["outcome"] == "lose"
    t.G.advance()                                            # settle -> betting
    assert t.seat("ann") is None                             # her seat is free once the hand is paid
    assert t.seat("bob")["state"] == "held"


# ---------------------------------------------------------------------------------------------------------
# the deal, the peek, insurance
# ---------------------------------------------------------------------------------------------------------

def test_the_deal_goes_round_the_table_twice_and_the_hole_card_stays_hidden(t):
    t.start(seats=6)
    t.bets(("ann", 10), ("bob", 10), ("cy", 10))
    # order: ann bob cy dealer-up, ann bob cy dealer-hole
    t.rig(["2S", "3S", "4S", "TD", "5H", "6H", "7H", "6C"])
    t.G.advance()
    v = t.view()
    assert v["phase"] == "dealing"
    assert [s["hands"][0]["cards"] for s in v["seats"] if s["user"]] == [["2S", "5H"], ["3S", "6H"], ["4S", "7H"]]
    assert v["dealer"]["cards"] == ["TD", None] and v["dealer"]["hidden"] and v["dealer"]["total"] == 10
    assert v["deal"]["order"] == [[1, 0, 0], [2, 0, 0], [3, 0, 0], [0, 0, 0], [1, 0, 1], [2, 0, 1], [3, 0, 1], [0, 0, 1]]
    assert "6C" not in json.dumps(v["dealer"]) and v["dealer"]["bj"] is None
    assert v["deal"]["t0"] >= 350 and v["deal"]["step"] == 240
    # the whole table's state never carries the hole card either
    assert json.dumps(t.G.state_view()).count('"6C"') == 0
    assert t.G.state_view()["game"]["rules"]["text"][1] == "Dealer stands on all 17s"


def test_a_player_blackjack_is_marked_at_the_deal_and_is_not_asked_to_act(t):
    t.start(seats=4)
    t.bets(("ann", 10), ("bob", 10))
    ph = t.deal(["AS", "9S", "7D", "KS", "5D", "9C"])        # ann A+K = blackjack, bob 9+5, dealer 7+9
    assert ph == "action"
    assert t.seat("ann")["hands"][0]["status"] == "blackjack" and t.seat("bob")["hands"][0]["status"] == "active"
    assert t.act("ann", "hit")[0] == 400                      # nothing to do
    t.ok("bob", "stand")
    assert t.G.phase == "action"
    t.finish_hand()
    assert t.result("ann") == {"outcome": "blackjack", "pay": 25, "net": 15}


def test_dealer_peeks_a_ten_up_and_ends_the_hand_on_blackjack(t):
    t.start(seats=4)
    t.bets(("ann", 100), ("bob", 100))
    ph = t.deal(["KS", "AS", "TD", "9D", "KH", "AH"])
    # ann K+9, bob A+K (blackjack); dealer T + A = blackjack: no insurance for a ten up, the dealer peeks at once
    assert ph == "dealer"
    d = t.view()["dealer"]
    assert d["cards"] == ["TD", "AH"] and d["bj"] is True and d["peeked"] is True and not d["hidden"]
    t.G.advance()                                             # dealer -> settle
    assert t.result("ann")["outcome"] == "lose"               # ann loses her ante
    assert t.result("bob")["outcome"] == "push"               # bob's blackjack pushes
    assert t.G.state_view()["game"]["last"]["dealer"]["bj"] is True
    assert t.player_net("ann") == -100 and t.player_net("bob") == 0


def test_dealer_peek_finds_no_blackjack_and_play_goes_on(t):
    t.start(seats=4)
    t.bets(("ann", 100))
    ph = t.deal(["9S", "TD", "8D", "9C"])                    # ann 17, dealer T + 9
    assert ph == "action"
    d = t.view()["dealer"]
    assert d["peeked"] is True and d["hidden"] and d["bj"] is False      # still hidden: it was only a peek (and it found nothing)
    assert t.view()["say"]["key"] == "peek_ok"


def test_insurance_flow_dealer_blackjack(t):
    t.start(seats=4)
    t.bets(("ann", 100), ("bob", 100), ("cy", 1))
    # ann 9+8, bob 9+T, cy 9+7; dealer A up, K down = blackjack
    ph = t.deal(["9S", "9H", "9C", "AD", "8S", "TD", "7D", "KH"])
    assert ph == "insurance"
    assert t.act("cy", "insurance")[0] == 400                 # a 1 coin bet can't be insured (insurance is half the bet)
    t.ok("ann", "insurance")
    t.ok("bob", "decline")
    assert t.G.phase == "insurance"
    t.G.advance()                                             # the window ends: the dealer peeks, has blackjack
    assert t.G.phase == "dealer" and t.view()["dealer"]["bj"] is True
    t.G.advance()
    assert t.G.phase == "settle"
    # ann: ante lost (-100), insurance 50 pays 2:1 (credit 150): net 0. bob: -100. cy: -1
    assert t.player_net("ann") == 0 and t.player_net("bob") == -100 and t.player_net("cy") == -1
    row = {r["user"]: r for r in t.view()["last"]["results"]}
    assert row["ann"]["insurance"] == {"bet": 50, "won": True} and row["ann"]["net"] == 0
    reasons = [(e["user"], e["reason"], e["amount"]) for e in t.events() if e["type"] == "credit"]
    assert reasons == [("ann", "insurance", 150)]


def test_insurance_is_lost_when_the_dealer_has_no_blackjack(t):
    t.start(seats=3)
    t.bets(("ann", 100))
    assert t.deal(["TS", "AD", "7S", "7D"]) == "insurance"      # ann T+7, dealer A + 7 = soft 18
    t.ok("ann", "insurance")
    t.G.advance()                                             # peek: dealer A + 7 is not blackjack
    assert t.G.phase == "action"
    t.ok("ann", "stand")
    t.finish_hand()
    # ann T+7 = 17 vs dealer soft 18 (A+7 stands on soft 18): ante lost, insurance lost
    assert t.player_net("ann") == -150
    assert [r["user"] for r in t.view()["last"]["results"]] == ["ann"]
    assert t.view()["last"]["results"][0]["insurance"] == {"bet": 50, "won": False}


def test_even_money_is_insurance_on_a_blackjack(t):
    t.start(seats=3)
    t.bets(("ann", 100))
    assert t.deal(["AS", "AD", "KD", "7C"]) == "insurance"
    # (ann A + K = blackjack; the dealer shows an ace over a 7)
    assert t.seat("ann")["hands"][0]["status"] == "blackjack"
    t.ok("ann", "insurance")
    t.G.advance()
    t.finish_hand()
    assert t.player_net("ann") == 100                         # even money: +1x the bet, whatever the dealer has


def test_no_insurance_when_the_rule_is_off_or_the_dealer_shows_no_ace(t):
    t.start(seats=3, insurance=False)
    t.bets(("ann", 100))
    assert t.deal(["9S", "AD", "8S", "5D"]) == "action"                                          # ace up but no insurance offered
    assert t.act("ann", "insurance")[0] == 409                                                  # (insurance_closed)


# ---------------------------------------------------------------------------------------------------------
# the dealer's play
# ---------------------------------------------------------------------------------------------------------

def dealer_hand(t, up, hole, draws, **cfg):
    t.start(seats=3, **cfg)
    t.bets(("ann", 10))
    t.deal(["KS", up, "8C", hole] + draws)
    if t.G.phase == "insurance":                              # (an ace up: let the window run out, the dealer peeks)
        t.G.advance()
    assert t.G.phase == "action"
    t.ok("ann", "stand")
    t.finish_hand()
    return t.view()["last"]["dealer"]


def test_dealer_draws_to_16_and_stands_on_17(t):
    d = dealer_hand(t, "TH", "6C", ["5D", "9S"])              # 16 -> draws 5 = 21
    assert d["cards"] == ["TH", "6C", "5D"] and d["total"] == 21


def test_dealer_stands_on_soft_17_by_default(t):
    d = dealer_hand(t, "AH", "6C", ["5D", "9S"])              # soft 17: stands
    assert d["cards"] == ["AH", "6C"] and d["total"] == 17


def test_dealer_hits_soft_17_when_the_rule_says_so(t):
    d = dealer_hand(t, "AH", "6C", ["5D", "9S"], dealer_hits_soft_17=True)     # soft 17 hits: A+6+5 is hard 12, hits again: 9
    assert d["cards"] == ["AH", "6C", "5D", "9S"] and d["total"] == 21


def test_dealer_busts_and_everybody_standing_wins(t):
    d = dealer_hand(t, "TH", "6C", ["KD", "2S"])              # 16 + K = 26 bust
    assert d["bust"] and d["total"] == 26
    assert t.result("ann")["outcome"] == "win"


def test_the_dealer_does_not_draw_when_nobody_is_left_to_beat(t):
    t.start(seats=3)
    t.bets(("ann", 10))
    t.deal(["6S", "TH", "6C", "6D", "KD"])                    # ann 12 hits a king and busts; the dealer has 16
    t.ok("ann", "hit")
    t.G.advance()                                             # the window ends: resolve
    assert t.G.phase == "resolve" and t.seat("ann")["hands"][0]["status"] == "bust"
    t.G.advance()
    assert t.G.phase == "dealer"
    d = t.view()["dealer"]
    assert d["cards"] == ["TH", "6D"] and not d["hidden"] and d["total"] == 16       # the hole card is turned, nothing drawn


# ---------------------------------------------------------------------------------------------------------
# simultaneous action rounds
# ---------------------------------------------------------------------------------------------------------

def test_every_seats_choice_is_resolved_at_the_end_of_the_window_in_seat_order(t):
    t.start(seats=6)
    t.bets(("ann", 10), ("bob", 10), ("cy", 10), ("dee", 10))
    # hands: ann 5+6=11, bob 9+9=18, cy 6+5=11, dee 10+3=13 ; dealer 10 + 7
    t.deal(["5S", "9S", "6S", "TS", "TD", "6D", "9D", "5D", "3C", "7C"] + ["2H", "3H", "4D"])
    assert t.G.phase == "action" and t.view()["round"] == 1
    t.ok("ann", "hit")
    t.ok("bob", "stand")
    t.ok("cy", "double")
    assert t.act("cy", "hit")[0] == 409                       # one choice per hand per round
    assert t.seat("cy")["hands"][0]["act"] == "double"       # (dee says nothing: she stands when the window ends)
    assert t.G.phase == "action"
    t.G.advance()                                             # the window ends
    assert t.G.phase == "resolve"
    v = t.view()
    # the cards came off the shoe in seat order: ann got 2H, cy (doubling) got 3H; bob and dee drew nothing
    assert v["seats"][0]["hands"][0]["cards"] == ["5S", "6D", "2H"]
    assert v["seats"][1]["hands"][0]["cards"] == ["9S", "9D"]
    assert v["seats"][2]["hands"][0]["cards"] == ["6S", "5D", "3H"]
    assert v["seats"][3]["hands"][0]["cards"] == ["TS", "3C"]
    assert v["deal"]["order"] == [[1, 0, 2], [3, 0, 2]]
    assert [h["status"] for s in v["seats"] if s["user"] for h in s["hands"]] == ["active", "stand", "stand", "stand"]
    assert v["seats"][2]["hands"][0]["bet"] == 20 and v["seats"][2]["hands"][0]["doubled"]
    # ann (13) is still live: another round
    t.G.advance()
    assert t.G.phase == "action" and t.view()["round"] == 2
    t.ok("ann", "stand")
    t.finish_hand()
    assert t.G.phase == "settle"


def test_the_round_closes_early_when_everybody_has_chosen(t):
    t.start(seats=4)
    t.bets(("ann", 10), ("bob", 10))
    t.deal(["9S", "9H", "TD", "8S", "8H", "7C"])
    before = t.G.g["ends_at"]
    t.ok("ann", "stand")
    assert t.G.g["ends_at"] == before                         # bob is still to choose
    t.ok("bob", "stand")
    assert t.G.g["ends_at"] < before                          # everybody has: the clock is cut short
    assert t.G.g["len"] == 12000                              # (the window's own length is kept for the countdown ring)


def test_silence_is_standing_and_a_busted_hand_is_not_asked_again(t):
    t.start(seats=4)
    t.bets(("ann", 10), ("bob", 10))
    t.deal(["TS", "TH", "7D", "6S", "6H", "7C"] + ["KD"])     # ann 16, bob 16, dealer 14
    t.ok("ann", "hit")                                        # ann hits, takes K: bust
    t.G.advance()
    assert t.seat("ann")["hands"][0]["status"] == "bust"
    t.G.advance()                                             # resolve over: bob (silent) stood, ann busted -> nobody live
    assert t.G.phase == "dealer"
    t.finish_hand()
    assert t.result("ann")["outcome"] == "bust" and t.G.g["round"] == 1


def test_rounds_repeat_until_everyone_is_done_and_max_rounds_ends_it(t):
    t.start(seats=3, max_rounds=2)
    t.bets(("ann", 10))
    t.deal(["2S", "TH", "2D", "6C"] + ["2C", "2H", "3S"])     # ann 2+2 vs dealer T+6
    t.ok("ann", "hit")
    t.G.advance(); t.G.advance()
    assert t.G.phase == "action" and t.view()["round"] == 2   # 2+2+2 = 6, still live
    t.ok("ann", "hit")
    t.G.advance(); t.G.advance()                              # round 2 was the last: she stands on whatever she has
    assert t.G.phase == "dealer"
    assert t.seat("ann")["hands"][0]["status"] == "stand"


def test_a_21_stands_by_itself(t):
    t.start(seats=3)
    t.bets(("ann", 10))
    t.deal(["5S", "TH", "6D", "9C"] + ["TD"])                 # 5+6 = 11, hits a ten: 21
    t.ok("ann", "hit")
    t.G.advance()
    assert t.seat("ann")["hands"][0]["status"] == "stand" and t.seat("ann")["hands"][0]["total"] == 21


def test_five_card_charlie_wins_automatically_when_the_rule_is_on(t):
    t.start(seats=3, five_card_charlie=True)
    t.bets(("ann", 10))
    t.deal(["2S", "TH", "2D", "9C"] + ["3C", "2H", "2S"])     # 2+2 then 3, 2, 2 -> five cards, 11
    for _ in range(3):
        t.ok("ann", "hit")
        t.G.advance()                                         # resolve
        t.G.advance() if t.G.phase == "resolve" else None
    assert t.seat("ann")["hands"][0]["status"] == "charlie"
    t.finish_hand()
    assert t.result("ann") == {"outcome": "charlie", "pay": 20, "net": 10}


def test_surrender_returns_half_when_enabled(t):
    t.start(seats=3, surrender=True)
    t.bets(("ann", 25))
    t.deal(["TS", "TH", "6D", "9C"])
    body = t.ok("ann", "surrender")
    assert body["cost"] == 0
    t.finish_hand()
    assert t.result("ann") == {"outcome": "surrender", "pay": 12, "net": -13}      # half, rounded down
    assert t.player_net("ann") == -13


def test_surrender_is_refused_by_default(t):
    t.start(seats=3)
    t.bets(("ann", 25))
    t.deal(["TS", "TH", "6D", "9C"])
    assert t.act("ann", "surrender")[0] == 400


# ---------------------------------------------------------------------------------------------------------
# doubles and splits: money moves at once
# ---------------------------------------------------------------------------------------------------------

def test_double_debits_at_once_and_takes_exactly_one_card(t):
    t.start(seats=3)
    t.bets(("ann", 50))
    t.deal(["5S", "TH", "6D", "7C"] + ["9H"])                 # 11, dealer 17
    body = t.ok("ann", "double")
    assert body["cost"] == 50 and body["debits"][0]["reason"] == "double" and body["debits"][0]["amount"] == 50
    assert t.balance() == (100, 0)
    assert t.seat("ann")["stake"] == 100 and t.seat("ann")["hands"][0]["bet"] == 100
    t.G.advance()
    h = t.seat("ann")["hands"][0]
    assert h["cards"] == ["5S", "6D", "9H"] and h["status"] == "stand" and h["total"] == 20
    t.finish_hand()
    assert t.result("ann") == {"outcome": "win", "pay": 200, "net": 100}
    assert t.player_net("ann") == 100


def test_double_on_three_cards_or_a_finished_hand_is_refused(t):
    t.start(seats=3)
    t.bets(("ann", 10))
    t.deal(["2S", "TH", "3D", "7C"] + ["2H", "9D"])
    t.ok("ann", "hit")
    t.G.advance(); t.G.advance()
    assert t.G.phase == "action" and t.view()["round"] == 2
    assert t.act("ann", "double")[0] == 400                   # three cards now


def test_double_with_a_balance_that_cannot_cover_it_is_refused_and_moves_nothing(t):
    t.start(seats=3)
    t.bets(("ann", 50))
    t.deal(["5S", "TH", "6D", "7C"])
    st, body = t.act("ann", "double", balance=30)
    assert st == 400 and "not enough" in body["error"]
    assert t.balance() == (50, 0) and t.seat("ann")["hands"][0]["act"] is None
    assert t.act("ann", "double", balance=50)[0] == 200


def test_split_makes_two_hands_each_with_its_own_stake_and_a_new_card(t):
    t.start(seats=3)
    t.bets(("ann", 20))
    t.deal(["8S", "TH", "8D", "9C"] + ["3H", "2S"])           # 8+8 vs dealer 19; the split deals 3 and 2
    body = t.ok("ann", "split")
    assert body["cost"] == 20 and body["debits"][0]["reason"] == "split"
    assert t.balance() == (40, 0)
    assert t.seat("ann")["stake"] == 40                       # the second stake is on the line already (refundable)
    t.G.advance()                                             # resolve the round
    hs = t.seat("ann")["hands"]
    assert [h["cards"] for h in hs] == [["8S", "3H"], ["8D", "2S"]] and [h["bet"] for h in hs] == [20, 20]
    assert all(h["split"] for h in hs)
    assert t.view()["deal"]["moves"] == [[1, 0, 1]] and t.view()["deal"]["order"] == [[1, 0, 1], [1, 1, 1]]
    assert hs[0]["uid"] != hs[1]["uid"]
    t.G.advance()
    assert t.G.phase == "action"
    t.ok("ann", "stand", hand=1)
    t.ok("ann", "stand", hand=2)
    t.finish_hand()
    assert [h["result"]["outcome"] for h in t.seat("ann")["hands"]] == ["lose", "lose"]     # 11 and 10 vs 19
    assert t.player_net("ann") == -40


def test_split_aces_get_one_card_each_and_21_is_not_a_blackjack(t):
    t.start(seats=3)
    t.bets(("ann", 20))
    t.deal(["AS", "TH", "AD", "7C"] + ["KS", "9H"])           # A+A vs dealer 17; the split aces draw K and 9
    t.ok("ann", "split")
    t.G.advance()
    hs = t.seat("ann")["hands"]
    assert [h["status"] for h in hs] == ["stand", "stand"]                  # one card each: done at once
    assert [h["total"] for h in hs] == [21, 20]
    t.finish_hand()
    assert t.seat("ann")["hands"][0]["result"] == {"outcome": "win", "pay": 40, "net": 20}      # 21 pays 1:1, not 3:2
    assert t.seat("ann")["hands"][1]["result"]["outcome"] == "win"          # 20 beats 17


def test_aces_cannot_be_resplit_by_default_but_can_when_allowed(t):
    cards = ["AS", "TH", "AD", "7C"] + ["AC", "9H"]           # the split draws another ace for the first hand
    t.start(seats=3)
    t.bets(("ann", 20))
    t.deal(cards)
    t.ok("ann", "split")
    t.G.advance()
    assert [h["status"] for h in t.seat("ann")["hands"]] == ["stand", "stand"]       # A+A again, but no resplit
    t.G.stop()
    t.start(seats=3, resplit_aces=True)
    t.bets(("ann", 20))
    t.deal(cards + ["8D", "8C"])
    t.ok("ann", "split")
    t.G.advance()
    assert [h["status"] for h in t.seat("ann")["hands"]] == ["active", "stand"]      # may split the new pair of aces
    t.G.advance()
    assert t.G.phase == "action"
    assert t.act("ann", "hit", hand=1)[0] == 400              # but still only one card per ace
    t.ok("ann", "split", hand=1)


def test_a_seat_splits_up_to_four_hands(t):
    t.start(seats=3)
    t.bets(("ann", 10))
    # 8+8 splits and takes two more 8s; both pairs split again (two splits in one round) -> four hands
    t.deal(["8S", "TH", "8D", "7C"] + ["8C", "8H"] + ["8S", "3S", "4S", "5S"])
    t.ok("ann", "split")
    t.G.advance(); t.G.advance()
    assert [h["cards"] for h in t.seat("ann")["hands"]] == [["8S", "8C"], ["8D", "8H"]]
    t.ok("ann", "split", hand=1)
    t.ok("ann", "split", hand=2)                              # (2 hands + 1 pending split = 3 < 4)
    assert t.balance()[0] == 10 + 10 + 10 + 10
    t.G.advance(); t.G.advance()
    hs = t.seat("ann")["hands"]
    assert [h["cards"] for h in hs] == [["8S", "8S"], ["8C", "3S"], ["8D", "4S"], ["8H", "5S"]]
    assert t.view()["deal"] is None and t.G.phase == "action"
    assert t.act("ann", "split", hand=1)[0] == 400            # a pair again, but four hands is the limit
    assert len({h["uid"] for h in hs}) == 4


def test_pending_splits_count_against_the_hand_limit(t):
    t.start(seats=3, max_hands=3)
    t.bets(("ann", 10))
    t.deal(["8S", "TH", "8D", "7C"] + ["8C", "8H"])
    t.ok("ann", "split")
    t.G.advance(); t.G.advance()
    t.ok("ann", "split", hand=1)                              # 2 hands -> 3
    st, body = t.act("ann", "split", hand=2)                  # would be a fourth
    assert st == 400 and "can't split" in body["error"]


def test_no_double_after_split_when_the_rule_is_off(t):
    t.start(seats=3, double_after_split=False)
    t.bets(("ann", 10))
    t.deal(["8S", "TH", "8D", "7C"] + ["3H", "2S"])
    t.ok("ann", "split")
    t.G.advance(); t.G.advance()
    assert t.G.phase == "action"
    assert t.act("ann", "double", hand=1)[0] == 400
    assert t.act("ann", "hit", hand=1)[0] == 200


def test_split_and_double_after_split_money(t):
    t.start(seats=3)
    t.bets(("ann", 10))
    t.deal(["8S", "TH", "8D", "7C"] + ["3H", "2S", "TD", "9D"])
    t.ok("ann", "split")                                      # -10
    t.G.advance(); t.G.advance()
    t.ok("ann", "double", hand=1)                             # 8+3 = 11: -10
    t.ok("ann", "stand", hand=2)
    t.G.advance(); t.G.advance() if t.G.phase == "resolve" else None
    t.finish_hand()
    assert t.balance()[0] == 10 + 10 + 10                     # ante, split, double
    hs = t.seat("ann")["hands"]
    assert hs[0]["bet"] == 20 and hs[1]["bet"] == 10


# ---------------------------------------------------------------------------------------------------------
# settlement and the ledger
# ---------------------------------------------------------------------------------------------------------

def test_a_full_hand_pays_every_outcome_and_the_ledger_balances(t):
    t.start(seats=6)
    t.bets(("ann", 100), ("bob", 50), ("cy", 25), ("dee", 10), ("eve", 40))
    # ann K+Q 20, bob 9+9 18, cy A+K blackjack, dee 5+6 11 (doubles), eve 7+9 16; dealer T + 7 = 17
    t.deal(["KS", "9S", "AS", "5S", "7S", "TD", "QS", "9D", "KC", "6C", "9C", "7D"] + ["9H"])
    t.ok("ann", "stand")
    t.ok("bob", "stand")
    t.ok("dee", "double")
    t.ok("eve", "stand")
    t.finish_hand()
    assert t.G.phase == "settle"
    res = {u: t.result(u) for u in ("ann", "bob", "cy", "dee", "eve")}
    assert res["ann"] == {"outcome": "win", "pay": 200, "net": 100}
    assert res["bob"] == {"outcome": "win", "pay": 100, "net": 50}
    assert res["cy"] == {"outcome": "blackjack", "pay": 62, "net": 37}
    assert res["dee"] == {"outcome": "win", "pay": 40, "net": 20}
    assert res["eve"] == {"outcome": "lose", "pay": 0, "net": -40}
    debits, credits = t.balance()
    assert debits == 100 + 50 + 25 + 10 + 10 + 40 and credits == 200 + 100 + 62 + 40
    g = t.G.g
    assert g["debits"] == debits and g["credits"] == credits           # the game's own books agree with the ledger
    assert g["tally"]["hands"] == 1 and g["tally"]["blackjacks"] == 1 and g["tally"]["doubles"] == 1
    assert g["tally"]["wins"] == 4 and g["tally"]["losses"] == 1
    for u, net in (("ann", 100), ("bob", 50), ("cy", 37), ("dee", 20), ("eve", -40)):
        assert t.player_net(u) == net
    assert [e["reason"] for e in t.events() if e["type"] == "credit"] == ["win", "win", "win", "win"]
    last = t.view()["last"]
    assert last["net"] == 100 + 50 + 37 + 20 - 40 and last["dealer"]["total"] == 17


def test_push_pays_the_stake_back_and_losses_pay_nothing(t):
    t.start(seats=3)
    t.bets(("ann", 30), ("bob", 30))
    t.deal(["TS", "9S", "TC", "8D", "9H", "8C"])
    # ann T+8 = 18, bob 9+9 = 18; dealer T+8 = 18
    t.ok("ann", "stand"); t.ok("bob", "stand")
    t.finish_hand()
    assert t.result("ann")["outcome"] == "push" and t.result("bob")["outcome"] == "push"
    assert t.balance() == (60, 60)
    assert [e["reason"] for e in t.events() if e["type"] == "credit"] == ["push", "push"]


def test_blackjack_pays_on_odd_bets_rounded_down_and_follows_the_setting(t):
    t.start(seats=3, blackjack_pays="6:5")
    t.bets(("ann", 25))
    t.deal(["AS", "TH", "KS", "9C"])
    t.finish_hand()
    assert t.result("ann") == {"outcome": "blackjack", "pay": 55, "net": 30}        # 25 * 6/5 = 30


def test_without_the_peek_a_dealer_blackjack_takes_the_original_bet_only(t):
    t.start(seats=3, dealer_peeks=False)
    t.bets(("ann", 50), ("bob", 50))
    # ann 5+6 doubles, bob T+7 stands; dealer T + A = blackjack found only at the end
    ph = t.deal(["5S", "TH", "TD", "6S", "7H", "AD"] + ["9D"])
    assert ph == "action"                                     # no peek: the hand is played
    t.ok("ann", "double")
    t.ok("bob", "stand")
    t.finish_hand()
    assert t.view()["last"]["dealer"]["bj"] is True
    assert t.player_net("ann") == -50 and t.player_net("bob") == -50          # the double comes back
    reasons = [(e["user"], e["reason"], e["amount"]) for e in t.events() if e["type"] == "credit"]
    assert reasons == [("ann", "refund", 50)]


def test_credits_are_not_issued_twice_and_stop_after_settlement_refunds_nothing(t):
    t.start(seats=3)
    t.bets(("ann", 100))
    t.deal(["TS", "TC", "9D", "8D"])                          # ann 19 vs dealer 18
    t.ok("ann", "stand")
    t.finish_hand()
    n = len(t.events())
    t.G.stop()
    assert len(t.events()) == n                               # the hand was paid: stopping gives back nothing more
    d, c = t.balance()
    assert d - c == t.G.history[0]["result"]["house_net"]


# ---------------------------------------------------------------------------------------------------------
# stop, close, restart
# ---------------------------------------------------------------------------------------------------------

def test_stopping_mid_hand_returns_every_stake_including_doubles_splits_and_insurance(t):
    t.start(seats=4)
    t.bets(("ann", 20), ("bob", 30))
    t.bet("cy", 10)
    t.deal(["8S", "9H", "5D", "AD", "8D", "2C", "6C", "7S"])   # ann 8+8, bob 9+2, cy 5+6, dealer A + 7
    assert t.G.phase == "insurance"
    t.ok("bob", "insurance")                                  # 15
    t.G.advance()                                             # peek: A + 7 no blackjack
    t.ok("ann", "split")                                      # 20 more
    t.ok("cy", "double")                                      # 10 more
    assert t.balance()[0] == 20 + 30 + 10 + 15 + 20 + 10
    t.G.stop()
    d, c = t.balance()
    assert d == c == 105                                      # everything came back
    assert t.player_net("ann") == 0 and t.player_net("bob") == 0 and t.player_net("cy") == 0
    assert t.G.g is None and t.G.history[0]["result"]["outcome"] == "stopped"


def test_stopping_during_betting_refunds_seats_and_the_queue(t):
    t.start(seats=1)
    t.bets(("ann", 20), ("bob", 30))
    t.G.stop()
    assert t.balance() == (50, 50)


def test_close_ends_the_table_after_the_hand(t):
    t.start(seats=3, bet_seconds=5)
    t.bets(("ann", 10))
    t.deal(["TS", "TH", "9D", "7C"])
    st, body = t.G.close({})
    assert st == 200 and body["closing"] is True
    assert t.view()["closing"] is True
    t.ok("ann", "stand")
    t.finish_hand()
    assert t.G.phase == "settle"
    t.G.advance()
    assert t.G.phase == "over" and t.G.g["outcome"] == "closed"
    t.G.advance()
    assert t.G.g is None


def test_close_between_hands_closes_right_away_and_refunds(t):
    t.start(seats=3)
    t.bets(("ann", 10))
    st, body = t.G.close({})
    assert body["closed"] and t.G.phase == "over"
    assert t.balance() == (10, 10)


def test_an_empty_table_closes_after_idle_windows(t):
    t.start(seats=3, idle_windows=2)
    t.G.advance()
    assert t.G.phase == "betting"                             # one empty window: wait for somebody
    t.G.advance()
    assert t.G.phase == "over" and t.G.g["outcome"] == "no_bets"


def test_a_restart_settles_a_running_hand_and_refunds(t):
    t.start(seats=3)
    t.bets(("ann", 40))
    t.deal(["5S", "TH", "6D", "7C"])                          # 11 vs 17: she doubles
    t.ok("ann", "double")
    saved = t.G.path.read_text(encoding="utf-8")
    assert '"phase": "action"' in saved
    debits, _ = t.balance()
    # a new process: the game file is read and the hand is settled
    fresh = t.bj.Blackjack()
    assert fresh.g is not None and fresh.g["phase"] == "action"
    fresh.resume()
    assert fresh.g is None
    d, c = t.balance()
    assert c == d == debits                                   # every coin that was on the line came back
    assert fresh.history[0]["result"]["outcome"] == "restart"


# ---------------------------------------------------------------------------------------------------------
# config and the edge
# ---------------------------------------------------------------------------------------------------------

def test_config_is_validated(t):
    G = t.G
    cfg = G.validate_config({"seats": 99, "queue_max": -5, "blackjack_pays": "7:5", "double_on": "9-11", "max_hands": 9,
                             "deck_table": "garbage", "penetration_pct": 5, "theme": "neon", "title": "x" * 80,
                             "min_bet": 50, "max_bet": 10, "card_ms": 1, "dealer_hits_soft_17": "yes"})
    assert cfg["seats"] == 14 and cfg["queue_max"] == 0 and cfg["blackjack_pays"] == "3:2" and cfg["double_on"] == "9-11"
    assert cfg["max_hands"] == 4 and cfg["deck_table"] == "1:2,3:4,5:6,8:8" and cfg["penetration_pct"] == 40
    assert cfg["theme"] == "neon" and len(cfg["title"]) == 32 and cfg["max_bet"] == 0 and cfg["card_ms"] == 100
    assert cfg["dealer_hits_soft_17"] is True
    assert G.validate_config({"deck_table": "1:1, 4:6"})["deck_table"] == "1:1,4:6"
    assert G.validate_config({})["currency"] == "hexcoins"
    assert set(G.validate_config({}).keys()) == set(G.DEFAULTS.keys())
    assert t.bj.OVERLAY["defaults"] == t.bj.Blackjack.DEFAULTS


def test_the_house_edge_is_computed_from_the_rules(t):
    bj = t.bj
    base = bj.rules_of(bj.Blackjack.DEFAULTS)
    e = lambda **kw: bj.house_edge({**base, **kw}, None)
    inf = e()
    assert 0.35 < inf < 0.65                                            # a normal Vegas edge for the default rules
    assert 0.35 < bj.house_edge(base, 8) < 0.55 and bj.house_edge(base, 2) < bj.house_edge(base, 4) < bj.house_edge(base, 8) < inf
    assert abs(bj.house_edge(base, 2) - (inf - 0.25)) < 1e-9
    # the textbook size of each rule (percentage points)
    assert 0.18 < e(dealer_hits_soft_17=True) - inf < 0.26
    assert 1.2 < e(blackjack_pays="6:5") - inf < 1.5
    assert 2.1 < e(blackjack_pays="1:1") - inf < 2.4
    assert -2.4 < e(blackjack_pays="2:1") - inf < -2.1
    assert 0.1 < e(double_after_split=False) - inf < 0.18
    assert 0.12 < e(double_on="10-11") - inf < 0.25 and 0.05 < e(double_on="9-11") - inf < 0.12
    assert -0.12 < e(resplit_aces=True) - inf < -0.05
    assert e(split_aces_one_card=False) < inf and e(surrender=True) < inf
    assert e(max_hands=1) > e(max_hands=2) > e(max_hands=3) >= e(max_hands=4) - 1e-9
    assert -1.8 < e(five_card_charlie=True) - inf < -1.2


def test_the_dealers_odds_match_the_standard_tables(t):
    bj = t.bj
    bust = {up: bj.dealer_distribution(up, False)[0][5] * 100 for up in range(1, 11)}
    for up, want in {2: 35.3, 3: 37.4, 4: 39.5, 5: 41.7, 6: 42.3, 7: 26.2, 8: 24.5, 9: 22.8}.items():
        assert abs(bust[up] - want) < 0.25, (up, bust[up])
    assert abs(bust[10] * 12 / 13 - 21.2) < 0.2 and abs(bust[1] * 9 / 13 - 11.5) < 0.3     # (without the blackjack share)
    assert abs(bj.dealer_distribution(1, False)[1] - 4 / 13) < 1e-9 and abs(bj.dealer_distribution(10, False)[1] - 1 / 13) < 1e-9
    assert bj.dealer_distribution(1, True)[0][5] > bj.dealer_distribution(1, False)[0][5]            # hitting soft 17 busts more


def test_rules_text_follows_the_settings(t):
    bj = t.bj
    R = bj.rules_of(bj.Blackjack.DEFAULTS)
    txt = bj.rules_text(R)
    assert txt[0] == "Blackjack pays 3 to 2" and txt[1] == "Dealer stands on all 17s" and "Insurance pays 2 to 1" in txt
    assert "Split up to 4 hands" in txt and "Double on any two cards" in txt and "No surrender" in txt
    txt = bj.rules_text({**R, "dealer_hits_soft_17": True, "blackjack_pays": "6:5", "surrender": True, "five_card_charlie": True,
                         "insurance": False, "max_hands": 1})
    assert txt[0] == "Blackjack pays 6 to 5" and txt[1] == "Dealer hits soft 17" and "Late surrender" in txt
    assert "No splitting" in txt and "No insurance" in txt and "Five-card Charlie wins" in txt


# ---------------------------------------------------------------------------------------------------------
# the add-on: registration, routes, the HTTP API
# ---------------------------------------------------------------------------------------------------------

def test_a_test_table_plays_for_nothing_and_leaves_no_trace_in_the_ledger_or_the_stats(t):
    t.set(seat_fill="first")
    st, body = t.G.start_game({"seats": 3, "test": True})
    assert st == 200 and t.view()["test"] is True
    t.bets(("ann", 100), ("bob", 50))
    t.deal(["TS", "9S", "9H", "8D", "7C", "TD"])
    t.ok("ann", "double")
    t.ok("bob", "stand")
    t.finish_hand()
    assert t.G.phase == "settle" and t.result("ann")["pay"] in (0, 200, 400) and t.result("bob")["outcome"] in ("win", "push", "lose")
    assert t.events() == []                                   # nothing reached the shared ledger ...
    assert t.G.g["log"], "the game keeps its own log of the coin movements"
    assert all(e["seq"] is None and e.get("test") for e in t.G.g["log"])
    t.G.stop()
    assert t.G.history[0]["test"] is True
    assert t.G._st["games"] == 0 and t.G._st["hands"] == 0 and t.G._st["total_bet"] == 0     # ... nor the stats


def test_the_shared_ledger_and_the_games_own_books_agree_over_many_random_hands_at_a_full_table(t):
    """Fourteen seats, a waiting list, random but legal choices (hits, doubles, splits, insurance, surrender), shoes
    that run out and are rebuilt: whatever happens, what the bank took minus what it paid is what the hands lost."""
    import random
    rng = random.Random(20240601)
    t.start(seats=14, seat_fill="center", surrender=True, resplit_aces=True, queue_max=6)
    bj, G = t.bj, t.G
    names = [f"p{i}" for i in range(20)]                      # 14 seats + 6 waiting
    net_of_hands = 0
    for hand in range(14):
        assert G.phase == "betting"
        for u in names:                                       # everybody (re)bets; new faces join, some stay away
            if rng.random() < 0.85:
                if G._seat_index(u) is not None and G.g["last_bets"].get(u):
                    G.rebet({"user": u})
                else:
                    t.bet(u, rng.choice([1, 5, 10, 25, 100, 250]))            # (a full queue just says no)
        if rng.random() < 0.3:                                # somebody gets up between hands
            G.leave({"user": rng.choice(names)})
        if not any(s and s["bet"] > 0 for s in G.g["seats"]):
            t.bet("p0", 10)
        G.advance()                                           # betting -> dealing
        guard = 0
        while G.phase not in ("settle", "betting", "over") and guard < 200:
            guard += 1
            if G.phase == "insurance":
                for s in G.g["seats"]:
                    if s and s["hands"] and rng.random() < 0.5:
                        G.act({"user": s["user"], "action": rng.choice(["insurance", "decline"])})
            elif G.phase == "action":
                for si, s in enumerate(G.g["seats"]):
                    if not s:
                        continue
                    for hi, h in enumerate(s["hands"]):
                        if h["status"] == "active" and not h["act"]:
                            n = len(s["hands"])
                            moves = ["stand"] + (["hit"] if bj.can_hit(h, G.g["rules"]) else []) * 3
                            moves += ["double"] if bj.can_double(h, G.g["rules"]) else []
                            moves += ["split"] * 2 if bj.can_split(h, n, G.g["rules"]) else []
                            moves += ["surrender"] if bj.can_surrender(h, n, G.g["rules"]) else []
                            G.act({"user": s["user"], "action": rng.choice(moves), "hand": hi + 1})
            G.advance()
        assert G.phase == "settle", G.phase
        view = G.state_view()["game"]
        assert all(len(s["hands"]) <= 4 for s in view["seats"] if s["user"])
        net_of_hands += G.g["last"]["net"]
        G.advance()                                           # settle -> the next betting window
    G.stop()                                                  # refunds the antes of an unplayed hand
    debits, credits = t.balance()
    assert debits - credits == -net_of_hands                  # money is conserved, hand by hand
    assert G.history[0]["result"]["house_net"] == debits - credits
    assert G.history[0]["result"]["hands"] == 14 and G.history[0]["result"]["tally"]["player_hands"] > 50


def test_the_panel_script_gives_every_control_of_its_cards_a_different_id():
    """The Seats card once reused the id of the "Seats for this table" box, which left the card empty."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "catalog" / "games_blackjack" / "static" / "blackjack_panel.js").read_text(encoding="utf-8")
    ids = re.findall(r"id\('([a-z0-9-]+)'\)", src.split("controls:")[1].split("apiRows:")[0])
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


def test_the_overlay_has_hexs_skeleton_hand_and_no_cartoon_dealer_and_names_are_never_cut_short():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "catalog" / "games_blackjack"
    js = (root / "static" / "blackjack.js").read_text(encoding="utf-8")
    assert "drawHexHand" in js and "'S TABLE" in js
    for gone in ("drawDealer", "_drawAvatar", "visor", "eyeshade", "headY"):
        assert gone not in js, gone
    assert "visor" not in (root / "static" / "blackjack_panel.js").read_text(encoding="utf-8")
    # a seat's name is only ever cut when it is 20+ characters long (and only after the plate and the font gave all they can)
    assert "name.length >= 20" in js and "_plateWidths" in js


def test_hexs_table_talk_is_written_from_hex(t):
    says = t.bj.SAYS
    assert set(says) >= {"open", "next", "shuffle", "deal", "insurance", "peek_bj", "peek_ok", "action", "more", "dealer",
                         "bust", "house", "players", "closed"}
    assert all(lines and all(isinstance(x, str) and x for x in lines) for lines in says.values())
    t.start(seats=3)
    assert t.view()["say"]["text"] in says["open"]


def test_it_registers_as_a_games_add_on_at_order_70(real_world):
    w = real_world
    w.installer.install("games")
    w.installer.install("games_blackjack")
    w.host.load_all()
    assert "games_blackjack" in w.host.loaded, w.host.errors
    c = TestClient(w.app)
    reg = {g["key"]: g for g in c.get("/games/api/registry").json()["games"]}
    g = reg["blackjack"]
    assert g["plugin"] == "games_blackjack" and g["order"] == 70 and g["title"] == "Blackjack"
    assert g["overlay"]["stateful"] is True and g["overlay"]["script"].startswith("/plugins/games_blackjack/static/blackjack.js")
    assert g["panel_js"].startswith("/plugins/games_blackjack/static/blackjack_panel.js")
    assert "x" in g["overlay"]["appearance"] and "show_shoe" in g["overlay"]["appearance"]
    assert g["overlay"]["defaults"]["seats"] == 10
    for url in (g["overlay"]["script"], g["panel_js"]):
        r = c.get(url)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"] and len(r.text) > 500, url
    api = c.get("/games/api").json()
    assert "blackjack" in api["games"] and api["blackjack"]["bet"] and api["blackjack"]["action"]
    st = c.get("/games/api/status").json()["games"]["blackjack"]
    assert st["state"] == "idle" and st["idle"]["rules"]["blackjack_pays"] == "3:2"
    assert c.get("/games/api/blackjack/spin").status_code == 400          # a round game has no /spin
    # the section of its own in config/games.json
    assert c.get("/games/api/config").json()["config"]["blackjack"]["seats"] == 10
    stopped, clean = asyncio.run(w.host.unload("games_blackjack"))
    assert stopped == ["games_blackjack"] and clean
    assert "blackjack" not in c.get("/games/api/status").json()["games"]


def test_the_http_api_plays_a_hand(real_world):
    w = real_world
    w.installer.install("games")
    w.installer.install("games_blackjack")
    w.host.load_all()
    bj = sys.modules["hexcast_plugins.games_blackjack.blackjack"]
    with TestClient(w.app) as c:
        u = "/games/api/blackjack"
        assert c.post(f"{u}/bet", json={"user": "ann", "amount": 10}).status_code == 409       # no table yet
        r = c.post(f"{u}/start", json={"seats": 5, "seconds": 60})
        assert r.status_code == 200 and r.json()["started"].startswith("bj-")
        assert c.post(f"{u}/start", json={}).status_code == 409
        r = c.post(f"{u}/bet", json={"user": "ann", "amount": 100})
        j = r.json()
        assert r.status_code == 200 and j["seat"] == 3 and j["debits"][0]["reason"] == "bet" and j["state"]["game"]["phase"] == "betting"
        assert c.get(f"{u}/bet?user=bob&amount=50&seat=1").json()["seat"] == 1
        assert c.post(f"{u}/bet", json={"user": "bob", "amount": 0}).status_code == 400
        assert c.post(f"{u}/action", json={"user": "ann", "action": "hit"}).json()["error"] == "actions_closed"
        seats = c.get(f"{u}/seats").json()
        assert [s["user"] for s in seats["seats"]] == ["bob", None, "ann", None, None] and seats["queue"] == []
        # rig the shoe, then let the table deal: /next ends the window now
        G = bj.BLACKJACK
        G.shoe = bj.Shoe(2)
        G.shoe.put_next(["9S", "TS", "8D", "9H", "7C", "TD"])             # bob 9+9, ann T+9, dealer 8+... (see below)
        assert c.post(f"{u}/next").json()["skipped"] == "betting"
        t = c.get(f"{u}/table").json()
        assert t["game"]["phase"] == "dealing"
        assert t["game"]["dealer"]["cards"][1] is None                    # the hole card is never sent
        c.post(f"{u}/next")                                               # dealing -> (no ace up) peek / action
        t = c.get(f"{u}/table").json()["game"]
        assert t["phase"] == "action" and t["round"] == 1
        me = c.get(f"{u}/user/ann").json()
        assert me["player"]["seat"] == 3 and me["can"][0]["hit"] and me["can"][0]["stand"]
        assert c.post(f"{u}/action", json={"user": "ann", "action": "stand"}).json()["action"] == "stand"
        assert c.post(f"{u}/action", json={"user": "ann", "action": "stand"}).status_code == 409
        assert c.post(f"{u}/stand", json={"user": "bob"}).json()["action"] == "stand"      # a shortcut route
        assert c.post(f"{u}/action", json={"user": "zed", "action": "hit"}).status_code == 400
        assert c.post(f"{u}/action", json={"user": "ann", "action": "fly"}).status_code == 400
        for _ in range(10):                                               # let the table run to the next betting window
            if c.get(f"{u}/table").json()["game"]["phase"] == "betting":
                break
            c.post(f"{u}/next")
        led = c.get(f"{u}/ledger").json()
        assert [e["reason"] for e in led["events"] if e["type"] == "debit"] == ["bet", "bet"]
        assert all(e["game"] == "blackjack" for e in led["events"])
        assert c.get("/games/api/ledger?game=blackjack").json()["events"] == led["events"]
        bets = c.get(f"{u}/bets").json()
        assert bets["house_edge_pct"] > 0 and bets["edge_by_decks"]["2"] < bets["edge_by_decks"]["8"] and bets["rules"]["text"]
        assert c.post(f"{u}/rebet", json={"user": "ann"}).json()["stake"] == 100
        assert c.post(f"{u}/leave", json={"user": "ann"}).json()["credits"] == [{"user": "ann", "amount": 100}]
        stop = c.post(f"{u}/stop").json()
        assert stop["ok"] and c.get(f"{u}/table").json()["game"] is None


def test_the_preview_shows_the_table_without_a_game(real_world):
    w = real_world
    w.installer.install("games")
    w.installer.install("games_blackjack")
    w.host.load_all()
    with TestClient(w.app) as c:
        r = c.post("/games/api/blackjack/preview", json={"overrides": {"theme": "neon", "scale": 0.5}, "seconds": 5})
        assert r.status_code == 200
        st = r.json()["state"]
        assert st["preview"]["overrides"] == {"theme": "neon", "scale": 0.5} and st["visible"] and st["game"] is None
        assert c.post("/games/api/blackjack/preview/clear").json()["cleared"] is True
