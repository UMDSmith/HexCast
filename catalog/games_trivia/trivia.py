"""Trivia - a game-show board with Open Trivia DB and your own lore (Games add-on).
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import re
import secrets
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hexcast_plugins.games.core import (
    COMMANDS_TEXT_MAX,
    HUB,
    Ledger,
    ROUND_PLAYERS_MAX,
    ROUND_TITLE_MAX,
    RoundGame,
    _BG_TASKS,
    _CTRL,
    _RNG,
    _SURROGATES,
    _aggregate_credits,
    _as_float,
    _atomic_write_text,
    _clean_user,
    _cut,
    _debits,
    _echo,
    _err,
    _ev,
    _flag,
    _floor,
    _ledger_reply,
    _params,
    _round_common,
    _round_players,
    _round_request,
    _strict_int,
    log,
)

# paths + limits that belong to this game
from hexcast_plugins.games.core import CONFIG_DIR

TRIVIA_PATH = CONFIG_DIR / "games_trivia.json"            # the running game + history (trivia)
TRIVIA_BANK_PATH = CONFIG_DIR / "games_trivia_bank.json"  # OpenTDB session token, question pool, asked list
TRIVIA_LORE_PATH = CONFIG_DIR / "games_trivia_lore.json"  # lore: the channel's own questions
LORE_LABEL_MAX = 24             # trivia: what the overlay calls your own questions (lore_label)

# --------------------------------------------------------------------------
# trivia: the question bank (Open Trivia DB + the channel's lore)
# --------------------------------------------------------------------------
# Open Trivia DB (https://opentdb.com, CC BY-SA 4.0): multiple-choice questions by
# difficulty and category, base64-encoded, one call per 5 s per IP (every call here
# goes through one lock that keeps that gap). A session token keeps questions from
# repeating; unasked questions are kept in a pool on disk; every question asked (either
# source) goes on the "asked" list, which is never asked again for `repeat_hours`.
# Lore: the channel's own questions (2-5 options), kept in games_trivia_lore.json. The
# overlay calls them by the config's `lore_label` ("Channel Lore"); a lore question
# without a category has category "" in the STATE and the overlay shows that label instead.

OPENTDB_URL = "https://opentdb.com"
OPENTDB_GAP_SECONDS = 5.3
TRIVIA_DIFFICULTIES = ("easy", "medium", "hard")
TRIVIA_POOL_MAX = 150           # unasked OpenTDB questions kept per difficulty
TRIVIA_LORE_MAX = 5000
TRIVIA_Q_MAX = 300              # question length
TRIVIA_A_MAX = 120              # answer length
TRIVIA_OPTIONS_MAX = 5
TRIVIA_VOTERS_MAX = 5000
TRIVIA_LETTERS = "ABCDE"
TRIVIA_LORE_OLD_CATEGORY = "Hex Lore"   # the category lore questions got when they had none (before lore_label)


def _qtext(v: Any, limit: int) -> str | None:
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = _cut(re.sub(r"\s+", " ", _CTRL.sub(" ", _SURROGATES.sub("", str(v)))).strip(), limit).strip()
    return s or None


def _b64(v: Any) -> str:
    try:
        return base64.b64decode(str(v or "")).decode("utf-8", "replace")
    except Exception:
        return ""


def trivia_question(raw: Any, source: str = "lore") -> dict | None:
    """One question, cleaned: {id, key, source, category, difficulty, question, correct,
    incorrect[1..4]} (None = unusable). `incorrect` may be a list or "a|b|c"."""
    if not isinstance(raw, dict):
        return None
    q = _qtext(raw.get("question"), TRIVIA_Q_MAX)
    c = _qtext(raw.get("correct", raw.get("correct_answer")), TRIVIA_A_MAX)
    inc = raw.get("incorrect", raw.get("incorrect_answers"))
    if isinstance(inc, str):
        inc = inc.split("|")
    if not q or not c or not isinstance(inc, list):
        return None
    wrong, seen = [], {c.lower()}
    for w in inc:
        t = _qtext(w, TRIVIA_A_MAX)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            wrong.append(t)
    wrong = wrong[:TRIVIA_OPTIONS_MAX - 1]
    if not wrong:
        return None
    d = str(raw.get("difficulty") or "medium").strip().lower()
    if d not in TRIVIA_DIFFICULTIES:
        d = "medium"
    key = hashlib.sha1(f"{q.lower()}|{c.lower()}".encode("utf-8")).hexdigest()[:16]
    out = {"id": ("lore-" if source == "lore" else "otdb-") + key[:10], "key": key, "source": source,
           "category": _qtext(raw.get("category"), 60) or ("" if source == "lore" else "General Knowledge"),
           "difficulty": d, "question": q, "correct": c, "incorrect": wrong}
    if source == "opentdb":
        out["cat_id"] = _strict_int(raw.get("cat_id")) or 0
    return out


class TriviaBank:
    def __init__(self, path: Path, lore_path: Path) -> None:
        self.path, self.lore_path = Path(path), Path(lore_path)
        self.token: str | None = None
        self.pool: dict[str, list[dict]] = {d: [] for d in TRIVIA_DIFFICULTIES}
        self.asked: dict[str, float] = {}      # question key -> when it was asked
        self.lore: list[dict] = []
        self.categories: list[dict] | None = None
        self.last_error: str | None = None
        self.last_fetch: float | None = None
        self._next_at = 0.0
        self._lock: asyncio.Lock | None = None
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except Exception:
            log.exception("[games] %s is unreadable - starting an empty question pool", self.path)
            raw = {}
        if isinstance(raw, dict):
            self.token = raw.get("token") if isinstance(raw.get("token"), str) else None
            pool = raw.get("pool") if isinstance(raw.get("pool"), dict) else {}
            for d in TRIVIA_DIFFICULTIES:
                qs = [trivia_question(x, "opentdb") for x in pool.get(d) or []]
                self.pool[d] = [q for q in qs if q][:TRIVIA_POOL_MAX]
            asked = raw.get("asked") if isinstance(raw.get("asked"), dict) else {}
            self.asked = {str(k)[:16]: float(v) for k, v in asked.items() if _as_float(v) is not None}
        try:
            lr = json.loads(self.lore_path.read_text(encoding="utf-8")) if self.lore_path.exists() else {}
        except Exception:
            log.exception("[games] %s is unreadable - the lore is empty until it is fixed (copy kept as .bad)",
                          self.lore_path)
            try:
                self.lore_path.with_name(self.lore_path.name + ".bad").write_bytes(self.lore_path.read_bytes())
            except OSError:
                pass
            lr = {}
        items = lr.get("questions") if isinstance(lr, dict) else lr
        keys: set[str] = set()
        for x in items if isinstance(items, list) else []:
            q = trivia_question(x, "lore")
            if q and q["key"] not in keys:
                keys.add(q["key"])
                self.lore.append(q)

    def save(self) -> None:
        _atomic_write_text(self.path, json.dumps({"version": 1, "token": self.token, "pool": self.pool,
                                                  "asked": self.asked}, indent=1))

    def save_lore(self) -> bool:
        items = [{k: q[k] for k in ("id", "category", "difficulty", "question", "correct", "incorrect")}
                 for q in self.lore]
        return _atomic_write_text(self.lore_path, json.dumps({"version": 1, "questions": items}, indent=1))

    # ---- asked list -------------------------------------------------------------

    def prune(self, hours: float) -> None:
        if hours and hours > 0:
            cut = time.time() - hours * 3600
            self.asked = {k: v for k, v in self.asked.items() if v >= cut}

    def mark_asked(self, q: dict) -> None:
        self.asked[q["key"]] = round(time.time(), 1)
        self.save()

    def clear_asked(self) -> int:
        n = len(self.asked)
        self.asked = {}
        self.save()
        return n

    # ---- OpenTDB ------------------------------------------------------------------

    async def _get(self, path: str, params: dict) -> dict:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            wait = self._next_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Hexcast-games"}) as c:
                    r = await c.get(OPENTDB_URL + path, params=params)
                    data = r.json()
            finally:
                self._next_at = time.monotonic() + OPENTDB_GAP_SECONDS
        return data if isinstance(data, dict) else {}

    async def _ensure_token(self) -> None:
        if self.token:
            return
        d = await self._get("/api_token.php", {"command": "request"})
        if d.get("response_code") == 0 and isinstance(d.get("token"), str):
            self.token = d["token"]
            self.save()

    async def fetch(self, difficulty: str, category: int, amount: int) -> int:
        """Add up to `amount` new questions of one difficulty (and category, 0 = any)
        to the pool. Returns how many were added (0 on any failure: last_error says why)."""
        amount = max(1, min(50, int(amount)))
        data: dict = {}
        try:
            await self._ensure_token()
            for _attempt in range(4):
                params: dict[str, Any] = {"amount": amount, "type": "multiple", "encode": "base64",
                                          "difficulty": difficulty}
                if category:
                    params["category"] = category
                if self.token:
                    params["token"] = self.token
                data = await self._get("/api.php", params)
                code = data.get("response_code")
                if code == 0:
                    break
                if code == 1 and amount > 3:          # not that many left for this query
                    amount = max(3, amount // 3)
                    continue
                if code == 3:                         # token unknown / expired
                    self.token = None
                    await self._ensure_token()
                    continue
                if code == 4:                         # this token has seen them all: start over
                    await self._get("/api_token.php", {"command": "reset", "token": self.token or ""})
                    continue
                if code == 5:                         # rate limited: the lock waits the gap
                    continue
                self.last_error = f"OpenTDB answered response_code {code} for {difficulty} (category {category or 'any'})"
                return 0
            else:
                self.last_error = f"OpenTDB kept refusing {difficulty} questions (category {category or 'any'})"
                return 0
        except Exception as exc:
            self.last_error = f"OpenTDB unreachable: {exc.__class__.__name__}: {exc}"
            log.warning("[games] trivia: %s", self.last_error)
            return 0
        have = {q["key"] for d in TRIVIA_DIFFICULTIES for q in self.pool[d]}
        added = 0
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            q = trivia_question({"question": _b64(item.get("question")), "correct": _b64(item.get("correct_answer")),
                                 "incorrect": [_b64(x) for x in item.get("incorrect_answers") or []],
                                 "difficulty": _b64(item.get("difficulty")), "category": _b64(item.get("category")),
                                 "cat_id": category}, "opentdb")
            if q is None or q["key"] in have or q["key"] in self.asked:
                continue
            have.add(q["key"])
            self.pool[q["difficulty"]].append(q)
            added += 1
        for d in TRIVIA_DIFFICULTIES:
            del self.pool[d][:-TRIVIA_POOL_MAX]
        self.last_error = None if added else f"OpenTDB had no new {difficulty} questions (category {category or 'any'})"
        self.last_fetch = time.time()
        self.save()
        return added

    async def load_categories(self) -> list[dict]:
        if self.categories is None:
            try:
                d = await self._get("/api_category.php", {})
                cats = [{"id": _strict_int(c.get("id")), "name": str(c.get("name"))[:60]}
                        for c in d.get("trivia_categories") or [] if isinstance(c, dict)]
                self.categories = [c for c in cats if c["id"]]
            except Exception as exc:
                self.last_error = f"OpenTDB unreachable: {exc.__class__.__name__}: {exc}"
                return []
        return self.categories

    def take_pool(self, difficulty: str, category: int) -> dict | None:
        lst = self.pool.get(difficulty) or []
        lst[:] = [q for q in lst if q["key"] not in self.asked]
        for i, q in enumerate(lst):
            if not category or q.get("cat_id") == category:
                lst.pop(i)
                self.save()
                return q
        return None

    def pool_count(self, difficulty: str, category: int) -> int:
        return sum(1 for q in self.pool.get(difficulty) or []
                   if q["key"] not in self.asked and (not category or q.get("cat_id") == category))

    # ---- lore (the channel's own questions) ---------------------------------------

    def take_lore(self, difficulty: str | None, exclude: set[str]) -> dict | None:
        free = [q for q in self.lore if q["key"] not in self.asked and q["key"] not in exclude]
        same = [q for q in free if q["difficulty"] == difficulty] if difficulty else free
        pick = same or free
        return copy.deepcopy(_RNG.choice(pick)) if pick else None

    def add_lore(self, entries: list) -> tuple[list[dict], list[dict]]:
        added, rejected = [], []
        keys = {q["key"] for q in self.lore}
        for e in entries:
            q = trivia_question(e, "lore")
            if q is None:
                rejected.append({"entry": _echo(e.get("question") if isinstance(e, dict) else e),
                                 "error": "needs question, correct and 1-4 incorrect answers"})
            elif q["key"] in keys:
                rejected.append({"entry": q["question"][:40], "error": "already in the lore"})
            elif len(self.lore) >= TRIVIA_LORE_MAX:
                rejected.append({"entry": q["question"][:40], "error": f"the lore is full ({TRIVIA_LORE_MAX})"})
            else:
                keys.add(q["key"])
                self.lore.append(q)
                added.append(q)
        if added:
            self.save_lore()
        return added, rejected

    def remove_lore(self, ids: list[str]) -> int:
        want = {str(i) for i in ids}
        before = len(self.lore)
        self.lore = [q for q in self.lore if q["id"] not in want]
        if len(self.lore) != before:
            self.save_lore()
        return before - len(self.lore)

    def status(self, category: int = 0) -> dict:
        return {"pool": {d: self.pool_count(d, 0) for d in TRIVIA_DIFFICULTIES},
                "pool_category": {d: self.pool_count(d, category) for d in TRIVIA_DIFFICULTIES} if category else None,
                "asked": len(self.asked), "lore": len(self.lore),
                "lore_unasked": sum(1 for q in self.lore if q["key"] not in self.asked),
                "token": bool(self.token), "last_error": self.last_error, "last_fetch": self.last_fetch}


TRIVIA_BANK = TriviaBank(TRIVIA_BANK_PATH, TRIVIA_LORE_PATH)


# --------------------------------------------------------------------------
# trivia: the game
# --------------------------------------------------------------------------
# `questions` questions (15), easy -> medium -> hard. Before each question a betting
# window shows its number, category and difficulty - never the question. Then the
# question and its 2-5 options; EVERYONE may answer (last answer counts) but only
# bettors are paid. The vote counts show when answers close (show_votes), then the
# answer. Pays (total return) by difficulty: easy x1.5, medium x2, hard x3, plus a
# streak bonus of +10% per question already won in a row, capped at max_multiplier x
# the coins the player put in. A winner then RIDES (the whole balance goes on the next
# question, streak kept) or CASHES OUT (credited; may bet fresh again) - one who does
# neither is cashed out when the window closes. A wrong (or no) answer loses the stake.

_TRIVIA_THEMES = ("hex", "gameshow", "neon")
_TRIVIA_OUTCOMES = {"complete": "Every question played", "walked": "Nobody left playing",
                    "no_bets": "No bets - no game", "no_questions": "Ran out of questions",
                    "stopped": "Game stopped - every stake returned", "restart": "Settled after a restart",
                    "error": "Settled after an error"}


def trivia_plan(n: int, mode: str) -> list[str]:
    """The difficulty of each question: ramp = easy thirds first (a remainder goes to
    the easier groups), or all one difficulty, or mixed at random."""
    if mode in TRIVIA_DIFFICULTIES:
        return [mode] * n
    if mode == "mixed":
        return [_RNG.choice(TRIVIA_DIFFICULTIES) for _ in range(n)]
    e, m = (n + 2) // 3, (n + 1) // 3
    return ["easy"] * e + ["medium"] * m + ["hard"] * (n - e - m)


def trivia_choice(v: Any, options: list) -> int | None:
    """A player's answer: a letter (A-E), a 1-based number, or the option's text."""
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    if isinstance(v, (int, float)):
        i = int(v) - 1 if float(v).is_integer() else -1
    else:
        s = str(v).strip().lower()
        t = s.strip("!.()[]:- ")
        if len(t) == 1 and t in "abcde":
            i = ord(t) - 97
        elif t.isdigit():
            i = int(t) - 1
        else:
            i = next((k for k, o in enumerate(options) if o.strip().lower() == s), -1)
    return i if 0 <= i < len(options) else None


class Trivia(RoundGame):
    key = "trivia"
    title = "Trivia"
    id_prefix = "tv"
    PHASES = ("betting", "question", "votes", "reveal", "over")
    STAT_KEYS = ("games", "complete", "walked", "no_bets", "no_questions", "stopped", "questions",
                 "right", "wrong", "total_bet", "total_paid", "house_net")

    DEFAULTS: dict[str, Any] = {
        # placement: board centre in % of the 1920x1080 stage; scale x the 1120x630 board
        "x": 50, "y": 50, "scale": 1.0,
        "theme": "hex",                 # hex | gameshow | neon
        "title": "TRIVIA",              # the board's title (your branding)
        "lore_label": "Channel Lore",   # what the overlay calls your own questions
        "show_rules": True, "show_players": True, "players_max": 8,
        "sfx": True, "sfx_volume": 0.5,
        "hide_when_idle": True,
        "commands_text": "",            # e.g. "!bet 100 · !a B · !ride · !cashout" (your bot's commands)
        "questions": 15,
        "difficulty": "ramp",           # ramp | easy | medium | hard | mixed
        "category": 0,                  # OpenTDB category id, 0 = any
        "lore": "mixed",                # off | mixed | only (lore: the channel's own questions)
        "lore_every": 5,                # mixed: every Nth question is lore (when there is some left)
        "repeat_hours": 12,             # an asked question isn't asked again for this long (0 = never again)
        "open_bet_seconds": 30,         # before question 1
        "between_seconds": 15,          # before every later question (ride / cash out / bet)
        "answer_seconds": 15,
        "show_votes": "before_reveal",  # before_reveal | live | off
        "votes_seconds": 3,
        "reveal_seconds": 5,
        "summary_seconds": 10,
        "pay_easy": 1.5, "pay_medium": 2.0, "pay_hard": 3.0,    # total return, stake included
        "streak_bonus_pct": 10,         # + per question already won in a row
        "max_multiplier": 50,           # a balance never passes this x the coins put in; 0 = no cap
        "currency": "coins",            # your bot's coin name, shown after amounts
        "min_bet": 1, "max_bet": 100000,
        "question_clip": "", "reveal_clip": "",                  # soundboard clips
    }
    SCHEMA: dict[str, tuple] = {
        "x": ("num", 0, 100), "y": ("num", 0, 100), "scale": ("num", 0.2, 5),
        "theme": ("enum", _TRIVIA_THEMES),
        "title": ("name", ROUND_TITLE_MAX), "lore_label": ("name", LORE_LABEL_MAX),
        "show_rules": ("bool",), "show_players": ("bool",), "players_max": ("int", 1, 20),
        "sfx": ("bool",), "sfx_volume": ("num", 0, 1), "hide_when_idle": ("bool",),
        "commands_text": ("str", COMMANDS_TEXT_MAX),
        "questions": ("int", 1, 50), "difficulty": ("enum", ("ramp", "easy", "medium", "hard", "mixed")),
        "category": ("int", 0, 1000), "lore": ("enum", ("off", "mixed", "only")), "lore_every": ("int", 1, 50),
        "repeat_hours": ("num", 0, 8760),
        "open_bet_seconds": ("num", 5, 300), "between_seconds": ("num", 5, 300),
        "answer_seconds": ("num", 5, 120), "show_votes": ("enum", ("before_reveal", "live", "off")),
        "votes_seconds": ("num", 1, 30), "reveal_seconds": ("num", 2, 30), "summary_seconds": ("num", 4, 60),
        "pay_easy": ("num", 1, 100), "pay_medium": ("num", 1, 100), "pay_hard": ("num", 1, 100),
        "streak_bonus_pct": ("num", 0, 100), "max_multiplier": ("num", 0, 1e6),
        "currency": ("name", 24), "min_bet": ("int", 1, 1e9), "max_bet": ("int", 0, 1e12),
        "question_clip": ("str", 200), "reveal_clip": ("str", 200),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "title", "lore_label", "show_rules", "show_players", "players_max",
                  "sfx", "sfx_volume")

    def __init__(self, path: Path | None = None, ledger: Ledger | None = None,
                 bank: TriviaBank | None = None) -> None:
        self.bank = bank if bank is not None else TRIVIA_BANK
        self._starting = False
        self._prefetch_task: asyncio.Task | None = None
        super().__init__(path, ledger)

    def default_path(self) -> Path:
        return TRIVIA_PATH

    def validate_config(self, raw: Any) -> dict:
        out = super().validate_config(raw)
        if out["max_bet"] and out["max_bet"] < out["min_bet"]:
            out["max_bet"] = 0
        return out

    # ---- saved game -----------------------------------------------------------

    def clean_game(self, raw: Any) -> dict | None:
        g = _round_common(raw, self.PHASES)
        if g is None:
            return None

        def pos(v):
            if not isinstance(v, dict):
                return None
            bal, basis = _strict_int(v.get("balance")), _strict_int(v.get("basis"))
            if bal is None or bal < 0 or basis is None or basis < 1:
                return None
            return {"balance": bal, "basis": basis, "streak": max(0, _strict_int(v.get("streak")) or 0),
                    "status": "won" if v.get("status") == "won" else "in", "fresh": bool(v.get("fresh"))}

        qs = raw.get("questions") if isinstance(raw.get("questions"), list) else []
        total = max(1, min(50, _strict_int(raw.get("total")) or len(qs) or 1))
        questions = []
        for q in (qs + [None] * total)[:total]:
            ok = isinstance(q, dict) and isinstance(q.get("options"), list) and _strict_int(q.get("answer")) is not None
            questions.append(q if ok else None)
        pays = raw.get("pays") if isinstance(raw.get("pays"), dict) else {}
        plan = [d if d in TRIVIA_DIFFICULTIES else "medium" for d in (raw.get("plan") or [])][:total]
        g.update({
            "total": total, "index": min(total - 1, max(0, _strict_int(raw.get("index")) or 0)),
            "plan": plan + ["medium"] * (total - len(plan)), "questions": questions,
            "lore_slots": [bool(x) for x in (raw.get("lore_slots") or [])][:total],
            "category": _strict_int(raw.get("category")) or 0,
            "lore": raw.get("lore") if raw.get("lore") in ("off", "mixed", "only") else "mixed",
            "pays": {k: _as_float(pays.get(k)) or d for k, d in
                     (("easy", 1.5), ("medium", 2.0), ("hard", 3.0), ("bonus_pct", 0.0), ("max_multiplier", 0.0))},
            "positions": _round_players(raw.get("positions"), pos),
            "answers": {u: a for u, a in (raw.get("answers") or {}).items()
                        if _clean_user(u) and _strict_int(a) is not None} if isinstance(raw.get("answers"), dict) else {},
            "results": [r for r in raw.get("results") or [] if isinstance(r, dict)],
            "opentdb": bool(raw.get("opentdb")),
        })
        return g

    # ---- questions ------------------------------------------------------------------

    def _deal(self, q: dict) -> dict:
        """A bank question -> a game question: options shuffled, the answer's index."""
        opts = [q["correct"]] + list(q["incorrect"])
        _RNG.shuffle(opts)
        return {"id": q["id"], "key": q["key"], "source": q["source"], "category": q["category"],
                "difficulty": q["difficulty"], "question": q["question"], "options": opts,
                "answer": opts.index(q["correct"])}

    def _need(self, g: dict, difficulty: str) -> int:
        n = sum(1 for i in range(g["total"]) if g["plan"][i] == difficulty and g["questions"][i] is None
                and not (g["lore_slots"][i] if i < len(g["lore_slots"]) else False))
        return min(50, max(3, n + 3))

    async def _prepare(self, g: dict, i: int) -> dict | None:
        d = g["plan"][i]
        lore_slot = g["lore_slots"][i] if i < len(g["lore_slots"]) else False
        exclude = {q["key"] for q in g["questions"] if q}
        q = None
        if lore_slot or g["lore"] == "only":
            q = self.bank.take_lore(d, exclude)
        if q is None and g["lore"] != "only":
            q = self.bank.take_pool(d, g["category"])
            if q is None:
                await self.bank.fetch(d, g["category"], self._need(g, d))
                q = self.bank.take_pool(d, g["category"])
        if q is None and g["lore"] != "off":
            q = self.bank.take_lore(None, exclude)          # anything left in the lore
        return self._deal(q) if q else None

    async def _prefetch(self, g: dict) -> None:
        try:
            for i in range(1, g["total"]):
                if self.g is not g:
                    return
                if g["questions"][i] is None:
                    q = await self._prepare(g, i)
                    if self.g is not g:
                        return
                    g["questions"][i] = q
                    if q and q["source"] == "opentdb":
                        g["opentdb"] = True
                    self.save()
        except Exception:
            log.exception("[games] trivia: getting the questions failed")

    # ---- API actions ------------------------------------------------------------------

    async def start_game(self, params: dict) -> tuple[int, dict]:
        if self.g is not None or self._starting:
            return 409, {"error": "a game is already running", "phase": self.phase}
        cfg = self.cfg
        n = _as_float(params.get("questions"))
        n = int(min(50, max(1, n))) if n is not None else cfg["questions"]
        mode = str(params.get("difficulty") or cfg["difficulty"]).lower()
        mode = mode if mode in ("ramp", "easy", "medium", "hard", "mixed") else cfg["difficulty"]
        cat = _as_float(params.get("category"))
        cat = int(cat) if cat is not None and cat >= 0 else cfg["category"]
        lore = str(params.get("lore") or cfg["lore"]).lower()
        lore = lore if lore in ("off", "mixed", "only") else cfg["lore"]
        every = max(1, cfg["lore_every"])
        plan = trivia_plan(n, mode)
        slots = [lore == "only" or (lore == "mixed" and (i + 1) % every == 0) for i in range(n)]
        self.bank.prune(float(cfg["repeat_hours"]))
        g = {"id": f"tv-{secrets.token_hex(4)}", "test": _flag(params.get("test")), "phase": "betting",
             "started_at": round(time.time(), 3), "phase_at": round(time.time(), 3), "ends_at": None,
             "total": n, "index": 0, "plan": plan, "lore_slots": slots, "category": cat, "lore": lore,
             "questions": [None] * n,
             "pays": {"easy": cfg["pay_easy"], "medium": cfg["pay_medium"], "hard": cfg["pay_hard"],
                      "bonus_pct": cfg["streak_bonus_pct"], "max_multiplier": cfg["max_multiplier"]},
             "positions": {}, "answers": {}, "results": [], "opentdb": False,
             "debits": 0, "credits": 0, "totals": {}, "log": [], "ever_bet": False,
             "outcome": None, "summary": None, "last": None, "currency": cfg["currency"]}
        self._starting = True
        try:
            q0 = await self._prepare(g, 0)
        finally:
            self._starting = False
        if self.g is not None:
            return 409, {"error": "a game is already running", "phase": self.phase}
        if q0 is None:
            why = self.bank.last_error or "no questions left"
            return 503, {"error": f"no question to start with: {why}. Add lore questions (POST /lore), or try again "
                                  f"(OpenTDB allows one call per 5 s)", "bank": self.bank.status(cat)}
        g["questions"][0] = q0
        g["opentdb"] = q0["source"] == "opentdb"
        self.g = g
        self.hidden = False
        secs = _as_float(params.get("seconds"))
        secs = min(300.0, max(5.0, secs)) if secs is not None else float(cfg["open_bet_seconds"])
        self._set_phase("betting", secs)
        self.save()
        self._prefetch_task = asyncio.get_running_loop().create_task(self._prefetch(g))
        _BG_TASKS.add(self._prefetch_task)
        self._prefetch_task.add_done_callback(_BG_TASKS.discard)
        return 200, {"started": g["id"]}

    def place(self, params: dict) -> tuple[int, dict]:
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        p = g["positions"].get(user)
        if p is not None and p["status"] == "won":
            return 409, {"error": f"@{user} has {p['balance']} {g['currency']} waiting - ride or cash out first"}
        if p is not None and not p["fresh"]:
            return 409, {"error": f"@{user} is riding {p['balance']} {g['currency']} - a ride can't be topped up "
                                  f"(cash out, then bet again)"}
        if p is None and len(g["positions"]) >= ROUND_PLAYERS_MAX:
            return 400, {"error": f"the game is full ({ROUND_PLAYERS_MAX} players)"}
        amt, err = self._coins(params.get("amount"), p["balance"] if p else 0)
        if err:
            return 400, {"error": err}
        n = g["index"] + 1
        if p is None:
            g["positions"][user] = {"balance": amt, "basis": amt, "streak": 0, "status": "in", "fresh": True}
        else:
            p["balance"] += amt
            p["basis"] += amt
        g["ever_bet"] = True
        logged = self._persist([_ev("debit", user, amt, "add" if p else "bet", f"{g['id']}/q{n}", f"Question {n}",
                                    g["id"])])
        return 200, {"amount": amt, "debits": _debits(logged), "player": self._player(user)}

    def ride(self, params: dict) -> tuple[int, dict]:
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed("rides")
        user = _clean_user(params.get("user"))
        p = g["positions"].get(user) if user else None
        if p is None or p["status"] != "won":
            return 400, {"error": f"@{user or '?'} has no winnings waiting to ride"}
        p["status"] = "in"
        self.save()
        return 200, {"player": self._player(user)}

    def cashout(self, params: dict) -> tuple[int, dict]:
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed("cashout")
        user = _clean_user(params.get("user"))
        p = g["positions"].get(user) if user else None
        if p is None:
            return 400, {"error": f"@{user or '?'} has nothing to cash out"}
        logged = self._persist(self._cashout_events([user]))
        return 200, {"credits": _aggregate_credits(logged, "amount"), "ledger": logged}

    def answer(self, params: dict) -> tuple[int, dict]:
        g = self.g
        if g is None or g["phase"] != "question":
            return self._closed("answers")
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        q = g["questions"][g["index"]]
        i = trivia_choice(params.get("answer", params.get("choice")), q["options"])
        if i is None:
            return 400, {"error": f"answer with a letter A-{TRIVIA_LETTERS[len(q['options']) - 1]}"}
        if user not in g["answers"] and len(g["answers"]) >= TRIVIA_VOTERS_MAX:
            return 400, {"error": "too many answers"}
        changed = user in g["answers"]
        g["answers"][user] = i
        return 200, {"answer": TRIVIA_LETTERS[i], "changed": changed, "bettor": user in g["positions"]}

    def _cashout_events(self, users: list[str], label: str = "Cash out") -> list[dict]:
        g = self.g
        events = []
        for u in users:
            p = g["positions"].pop(u, None)
            if p and p["balance"] > 0:
                reason = "refund" if p["fresh"] else "cashout"
                events.append(_ev("credit", u, p["balance"], reason, f"{g['id']}/q{g['index'] + 1}",
                                  "Bet (taken back)" if p["fresh"] else label, g["id"]))
        return events

    def user_view(self, name: Any) -> dict:
        user = _clean_user(name)
        g = self.g
        player = self._player(user) if g is not None and user in g["positions"] else None
        answer = None
        if g is not None and user in g.get("answers", {}):
            answer = TRIVIA_LETTERS[g["answers"][user]]
        return {"user": user, "player": player, "answer": answer, "session": self.ledger.session(user, self.key)}

    # ---- the game -------------------------------------------------------------------

    def _mult(self, difficulty: str, streak: int) -> Fraction:
        p = self.g["pays"]
        base = Fraction(str(p.get(difficulty, 2.0)))
        return base * (1 + Fraction(str(p.get("bonus_pct", 0))) / 100 * streak)

    def advance(self) -> None:
        ph = self.g["phase"]
        if ph == "betting":
            self._open_question()
        elif ph == "question":
            if self.cfg["show_votes"] == "before_reveal":
                self._set_phase("votes", float(self.cfg["votes_seconds"]))
                self.save()
            else:
                self._reveal()
        elif ph == "votes":
            self._reveal()
        elif ph == "reveal":
            self._after_reveal()
        else:
            self._to_idle()

    def _open_question(self) -> None:
        g = self.g
        waiting = [u for u, p in g["positions"].items() if p["status"] == "won"]
        events = self._cashout_events(waiting, f"Cash out · after question {g['index']}")
        if not g["positions"]:
            self._finish("walked" if g["ever_bet"] else "no_bets", events)
            return
        q = g["questions"][g["index"]]
        if q is None:
            q = self._take_now(g["index"])
        if q is None:
            events += self._cashout_events(list(g["positions"]), "Cash out · no question")
            self._finish("no_questions", events)
            return
        for p in g["positions"].values():
            p["fresh"] = False                       # on the line now
        g["answers"] = {}
        self.bank.mark_asked(q)
        self._set_phase("question", float(self.cfg["answer_seconds"]))
        self._persist(events)
        self._clip("question_clip")

    def _take_now(self, i: int) -> dict | None:
        """The prefetch hasn't delivered question i: whatever the pool / lore has now."""
        g = self.g
        d = g["plan"][i]
        exclude = {q["key"] for q in g["questions"] if q}
        q = None
        if g["lore"] != "only":
            q = self.bank.take_pool(d, g["category"]) or self.bank.take_pool(d, 0)
        if q is None and g["lore"] != "off":
            q = self.bank.take_lore(d, exclude)
        if q is None:
            return None
        g["questions"][i] = self._deal(q)
        return g["questions"][i]

    def _reveal(self) -> None:
        g = self.g
        i = g["index"]
        q = g["questions"][i]
        right, wrong = [], []
        cap = Fraction(str(g["pays"].get("max_multiplier") or 0))
        for u, p in list(g["positions"].items()):
            if g["answers"].get(u) == q["answer"]:
                new = _floor(p["balance"] * self._mult(q["difficulty"], p["streak"]))
                if cap:
                    new = min(new, _floor(p["basis"] * cap))
                right.append({"user": u, "was": p["balance"], "balance": new, "streak": p["streak"] + 1})
                p.update(balance=new, streak=p["streak"] + 1, status="won")
            else:
                a = g["answers"].get(u)
                wrong.append({"user": u, "lost": p["balance"], "answer": TRIVIA_LETTERS[a] if a is not None else None})
                del g["positions"][u]
        right.sort(key=lambda r: -r["balance"])
        wrong.sort(key=lambda r: -r["lost"])
        votes = self._votes()
        voters_right = sum(1 for a in g["answers"].values() if a == q["answer"])
        res = {"number": i + 1, "difficulty": q["difficulty"], "source": q["source"],
               "correct": TRIVIA_LETTERS[q["answer"]], "votes": votes, "voters": len(g["answers"]),
               "voters_right": voters_right, "right": right, "wrong": wrong}
        g["results"].append({k: res[k] for k in ("number", "difficulty", "source", "correct", "voters", "voters_right")}
                            | {"bettors_right": len(right), "bettors_wrong": len(wrong)})
        g["last"] = res
        self._set_phase("reveal", float(self.cfg["reveal_seconds"]))
        self.save()
        self._clip("reveal_clip")

    def _after_reveal(self) -> None:
        g = self.g
        if g["index"] >= g["total"] - 1:
            self._finish("complete", self._cashout_events(list(g["positions"]), "Cash out · final"))
            return
        g["index"] += 1
        g["answers"] = {}
        self._set_phase("betting", float(self.cfg["between_seconds"]))
        self.save()

    def _finish(self, outcome: str, events: list[dict] | None = None) -> None:
        g = self.g
        if events:
            self._persist(events)
        g["outcome"] = outcome
        g["summary"] = self.summary(outcome)
        self._record_game(g["summary"])
        secs = float(self.cfg["summary_seconds"])
        self._set_phase("over", min(secs, 6.0) if outcome == "no_bets" else secs)
        self.save()

    def open_settlement(self) -> list[dict]:
        g = self.g
        if g["phase"] == "votes":
            self._reveal()                          # the answers were locked: the question counts
        # still on an unanswered question: stakes go back (a ride gets its balance back)
        return self._cashout_events(list(g["positions"]), "Cash out · game stopped")

    def summary(self, outcome: str) -> dict:
        g = self.g
        asked = len(g["results"])
        return {"outcome": outcome, "text": _TRIVIA_OUTCOMES.get(outcome, outcome), "questions": asked,
                "total": g["total"], "total_bet": g["debits"], "total_paid": g["credits"],
                "house_net": g["debits"] - g["credits"], "players": self.players_summary(),
                "right": sum(r.get("bettors_right", 0) for r in g["results"]),
                "wrong": sum(r.get("bettors_wrong", 0) for r in g["results"]),
                "test": bool(g.get("test")), "currency": g["currency"]}

    def record(self, s: dict) -> None:
        st = self._st
        st["games"] += 1
        st[s["outcome"] if s["outcome"] in ("complete", "walked", "no_bets", "no_questions") else "stopped"] += 1
        st["questions"] += s["questions"]
        st["right"] += s["right"]
        st["wrong"] += s["wrong"]
        st["total_bet"] += s["total_bet"]
        st["total_paid"] += s["total_paid"]
        st["house_net"] += s["house_net"]

    # ---- views --------------------------------------------------------------------

    def _votes(self) -> list[int]:
        g = self.g
        q = g["questions"][g["index"]]
        out = [0] * len(q["options"]) if q else []
        for a in g["answers"].values():
            if 0 <= a < len(out):
                out[a] += 1
        return out

    def _player(self, user: str) -> dict:
        g = self.g
        p = g["positions"][user]
        q = g["questions"][g["index"]]
        nxt = None
        if q and p["status"] in ("in", "won"):
            m = self._mult(q["difficulty"], p["streak"])
            nxt = _floor(p["balance"] * m)
            cap = Fraction(str(g["pays"].get("max_multiplier") or 0))
            if cap:
                nxt = min(nxt, _floor(p["basis"] * cap))
        return {"user": user, "status": p["status"], "balance": p["balance"], "basis": p["basis"],
                "streak": p["streak"], "fresh": p["fresh"], "if_right": nxt,
                "answered": g["phase"] == "question" and user in g["answers"]}

    def players_view(self) -> list[dict]:
        rows = [self._player(u) for u in self.g["positions"]]
        rows.sort(key=lambda p: (-p["balance"], p["user"].lower()))
        return rows

    def table_bets(self) -> list[dict]:
        if self.g is None:
            return []
        return [{"user": u, "amount": p["balance"]} for u, p in self.g["positions"].items()]

    def _category(self, q: dict) -> str:
        """A question's category in the STATE. A lore question without one (or with the old
        automatic "Hex Lore") gets "": the overlay shows the lore label in its place - the
        label it is drawing with, which during a Test in OBS is the previewed one, not the
        saved one."""
        cat = q.get("category") or ""
        if q.get("source") == "lore" and cat in ("", TRIVIA_LORE_OLD_CATEGORY):
            return ""
        return cat

    def game_view(self, now: float) -> dict:
        g, cfg = self.g, self.cfg
        i = g["index"]
        q = g["questions"][i]
        ph = g["phase"]
        head = {"number": i + 1, "category": self._category(q), "difficulty": q["difficulty"],
                "source": q["source"]} if q else {"number": i + 1, "category": None,
                                                  "difficulty": g["plan"][i], "source": None}
        question = None
        if q and ph in ("question", "votes", "reveal") or (q and ph == "over" and g["last"]
                                                          and g["last"]["number"] == i + 1):
            question = {**head, "text": q["question"], "options": list(q["options"]),
                        "answer": q["answer"] if ph in ("reveal", "over") else None}
        show_votes = ph in ("votes", "reveal") or (ph == "question" and cfg["show_votes"] == "live") or \
            (ph == "over" and question is not None)
        if cfg["show_votes"] == "off" and ph == "question":
            show_votes = False
        pays = g["pays"]
        players = self.players_view()
        return {"id": g["id"], "test": g["test"], "phase": ph, **self.timing(now),
                "number": i + 1, "total": g["total"], "plan": list(g["plan"]),
                "next": head if ph == "betting" else None, "question": question,
                "votes": self._votes() if show_votes and q else None, "voters": len(g["answers"]),
                "players": players, "on_the_line": sum(p["balance"] for p in players if p["status"] == "in"),
                "waiting": sum(p["balance"] for p in players if p["status"] == "won"),
                "pays": {"easy": pays["easy"], "medium": pays["medium"], "hard": pays["hard"],
                         "bonus_pct": pays["bonus_pct"], "max_multiplier": pays["max_multiplier"]},
                "results": [{"number": r["number"], "difficulty": r["difficulty"], "correct": r["correct"]}
                            for r in g["results"]],
                "last": g["last"], "outcome": g.get("outcome"), "summary": g.get("summary"),
                "opentdb": g.get("opentdb", False), "currency": g["currency"],
                "min_bet": cfg["min_bet"], "max_bet": cfg["max_bet"], "commands_text": cfg["commands_text"]}

    def idle_view(self) -> dict:
        cfg = self.cfg
        return {"questions": cfg["questions"], "difficulty": cfg["difficulty"], "lore": cfg["lore"],
                "pays": {"easy": cfg["pay_easy"], "medium": cfg["pay_medium"], "hard": cfg["pay_hard"],
                         "bonus_pct": cfg["streak_bonus_pct"], "max_multiplier": cfg["max_multiplier"]},
                "currency": cfg["currency"]}

    def bets_payload(self) -> dict:
        cfg = self.cfg
        return {"ok": True, "game": self.key, "currency": cfg["currency"], "min_bet": cfg["min_bet"],
                "max_bet": cfg["max_bet"],
                "pays": {"easy": cfg["pay_easy"], "medium": cfg["pay_medium"], "hard": cfg["pay_hard"],
                         "streak_bonus_pct": cfg["streak_bonus_pct"], "max_multiplier": cfg["max_multiplier"]},
                "rules": [
                    "Bet BEFORE the question: the betting window shows its number, category and difficulty only.",
                    f"Answer with a letter (A-E), a number or the option's text. Everyone may answer (the last "
                    f"answer counts); only bettors are paid. A wrong or missing answer loses the stake.",
                    f"A right answer pays (total, stake included) easy x{cfg['pay_easy']}, medium "
                    f"x{cfg['pay_medium']}, hard x{cfg['pay_hard']}, +{cfg['streak_bonus_pct']}% per question "
                    f"already won in a row" + (f", never more than x{cfg['max_multiplier']} the coins put in"
                                               if cfg["max_multiplier"] else "") + ".",
                    "A winner then rides (the whole balance on the next question) or cashes out (and may bet "
                    "fresh). One who does neither is cashed out when the window closes.",
                    "A ride can't be topped up: cash out, then bet again.",
                    "Every bet is against the bank: /bet debits, /cashout credits (the ledger).",
                    "No bets when a window closes: the game ends."]}

    def validate(self, params: dict) -> dict:
        amt, err = self._coins(params.get("amount"))
        return {"valid": err is None, "amount": amt, **({"error": err} if err else {})}


TRIVIA = Trivia()                    # restores a running game from games_trivia.json (settled by the plugin)


# --------------------------------------------------------------------------
# what the Games pages need to load this game (see core.register_game)
# --------------------------------------------------------------------------

OVERLAY = {
    "script": "trivia.js",
    "stateful": True,
    "appearance": list(Trivia.APPEARANCE),
    # mirrors the defaults of the game's config section, for a renderer that runs before its
    # first config message arrives
    "defaults": {
        "x": 50,
        "y": 50,
        "scale": 1.0,
        "theme": "hex",
        "title": "TRIVIA",
        "lore_label": "Channel Lore",
        "show_rules": True,
        "show_players": True,
        "players_max": 8,
        "sfx": True,
        "sfx_volume": 0.5,
        "hide_when_idle": True,
        "commands_text": "",
        "currency": "coins",
    },
}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/games", tags=["games"])


@router.api_route("/api/trivia/start", methods=["GET", "POST"])
async def api_trivia_start(request: Request):
    params, err = await _params(request)
    if err is not None:
        return err
    status, body = await TRIVIA.start_game(params)
    if status != 200:
        return _err(body.pop("error"), status, **body)
    await HUB.broadcast_state(TRIVIA)
    return {"ok": True, **body, "state": TRIVIA.state_view()}


@router.api_route("/api/trivia/bet", methods=["GET", "POST"])
async def api_trivia_bet(request: Request):
    return await _round_request(TRIVIA, request, TRIVIA.place)


@router.api_route("/api/trivia/answer", methods=["GET", "POST"])
async def api_trivia_answer(request: Request):
    return await _round_request(TRIVIA, request, TRIVIA.answer, quick=True)


@router.api_route("/api/trivia/ride", methods=["GET", "POST"])
async def api_trivia_ride(request: Request):
    return await _round_request(TRIVIA, request, TRIVIA.ride)


@router.api_route("/api/trivia/cashout", methods=["GET", "POST"])
@router.api_route("/api/trivia/remove", methods=["GET", "POST"])
async def api_trivia_cashout(request: Request):
    return await _round_request(TRIVIA, request, TRIVIA.cashout)


@router.api_route("/api/trivia/next", methods=["GET", "POST"])
async def api_trivia_next(request: Request):
    return await _round_request(TRIVIA, request, lambda _p: TRIVIA.skip())


@router.get("/api/trivia/table")
async def api_trivia_table():
    return {"ok": True, **TRIVIA.state_view()}


@router.get("/api/trivia/user/{name}")
async def api_trivia_user(name: str):
    return {"ok": True, **TRIVIA.user_view(name)}


@router.get("/api/trivia/ledger")
async def api_trivia_ledger(since: str | None = None, limit: str | None = None):
    return _ledger_reply(since, limit, "trivia")


@router.get("/api/trivia/lore")
async def api_trivia_lore_list():
    return {"ok": True, "count": len(TRIVIA_BANK.lore), "questions": TRIVIA_BANK.lore}


@router.post("/api/trivia/lore")
async def api_trivia_lore_add(request: Request):
    params, err = await _params(request)
    if err is not None:
        return err
    entries = params.get("questions")
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        entries = [params] if params.get("question") else []
    if not entries:
        return _err('lore needs {question, correct, incorrect:[...], difficulty, category} or {"questions": [...]}')
    added, rejected = TRIVIA_BANK.add_lore(entries[:TRIVIA_LORE_MAX])
    ok = bool(added)
    body = {"ok": ok, "added": added, "rejected": rejected, "count": len(TRIVIA_BANK.lore)}
    if not ok:
        body["error"] = rejected[0]["error"] if len(rejected) == 1 else "every question was rejected"
    return JSONResponse(body, status_code=200 if ok else 400)


@router.api_route("/api/trivia/lore/remove", methods=["GET", "POST"])
async def api_trivia_lore_remove(request: Request):
    params, err = await _params(request)
    if err is not None:
        return err
    ids = params.get("ids")
    ids = ids if isinstance(ids, list) else ([params.get("id")] if params.get("id") else [])
    if not ids:
        return _err("lore/remove needs id or ids")
    removed = TRIVIA_BANK.remove_lore(ids)
    return {"ok": True, "removed": removed, "count": len(TRIVIA_BANK.lore)}


@router.get("/api/trivia/categories")
async def api_trivia_categories():
    cats = await TRIVIA_BANK.load_categories()
    return {"ok": bool(cats), "categories": cats, **({"error": TRIVIA_BANK.last_error} if not cats else {})}


@router.get("/api/trivia/bank")
async def api_trivia_bank():
    TRIVIA_BANK.prune(float(TRIVIA.cfg["repeat_hours"]))
    return {"ok": True, **TRIVIA_BANK.status(TRIVIA.cfg["category"])}


@router.api_route("/api/trivia/asked/clear", methods=["GET", "POST"])
async def api_trivia_asked_clear():
    return {"ok": True, "cleared": TRIVIA_BANK.clear_asked(), **TRIVIA_BANK.status(TRIVIA.cfg["category"])}


API_DOC = {
    "about":    "one game at a time: betting -> question -> votes -> reveal -> ... -> over. Bet BEFORE the "
                "question; everyone may answer, only bettors are paid; docs/trivia.md",
    "start":    "GET|POST /games/api/trivia/start   {questions, difficulty, category, lore, seconds, test} -> "
                "{started, state}; 503 when no question could be had (OpenTDB down and no lore)",
    "bet":      "GET|POST /games/api/trivia/bet   {user, amount} -> {debits, player, state}",
    "answer":   "GET|POST /games/api/trivia/answer   {user, answer: A-E | 1-5 | option text} -> {answer, changed, bettor}",
    "ride":     "GET|POST /games/api/trivia/ride   {user}: a winner lets the balance ride",
    "cashout":  "GET|POST /games/api/trivia/cashout   (alias /remove) {user}: take the balance (a fresh bet: refund)",
    "next":     "GET|POST /games/api/trivia/next   end the current phase now",
    "table":    "GET /games/api/trivia/table   the STATE (the answer only from the reveal on)",
    "lore":     "GET /games/api/trivia/lore · POST {question, correct, incorrect[], difficulty, category} | "
                "{questions:[...]} · POST /lore/remove {id | ids}",
    "bank":     "GET /games/api/trivia/bank · GET /categories · POST /asked/clear (a new night)",
    "ledger":   "GET /games/api/trivia/ledger?since=0   reasons: bet, add, cashout, refund",
    "stop":     "GET|POST /games/api/trivia/stop   end the game: stakes refunded, winnings cashed out",
    "preview":  "GET|POST /games/api/trivia/preview   {overrides:{appearance}, seconds (2-60, default 8)}: "
                "Test in OBS, like russian's; /preview/clear ends it",
}
