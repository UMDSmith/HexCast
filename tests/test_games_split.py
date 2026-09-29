"""The Games plugin and its four add-ons, running from the real catalog/ folder."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

GAMES = ["games_roulette", "games_craps", "games_russian", "games_trivia"]


def install_all(w):
    w.installer.install("games")
    for g in GAMES:
        w.installer.install(g)
    w.host.load_all()


def registry(c):
    return [g["key"] for g in c.get("/games/api/registry").json()["games"]]


def test_all_games_register_in_tab_order(real_world):
    install_all(real_world)
    assert set(real_world.host.loaded) == {"games", *GAMES}, real_world.host.errors
    c = TestClient(real_world.app)
    reg = c.get("/games/api/registry").json()
    assert [g["key"] for g in reg["games"]] == ["roulette", "craps", "russian", "trivia"]
    for g in reg["games"]:
        assert g["overlay"]["script"].startswith(f"/plugins/games_{g['key']}/static/{g['key']}.js")
        assert g["panel_js"].startswith(f"/plugins/games_{g['key']}/static/{g['key']}_panel.js")
        assert g["overlay"]["appearance"] and g["overlay"]["defaults"]
    assert [g["key"] for g in reg["games"] if g["overlay"]["stateful"]] == ["russian", "trivia"]
    assert set(c.get("/games/api/status").json()["games"]) == {"roulette", "craps", "russian", "trivia"}


def test_games_host_alone_has_no_games(real_world):
    real_world.installer.install("games")
    real_world.host.load_all()
    c = TestClient(real_world.app)
    assert registry(c) == []
    assert c.get("/games/api/status").json()["games"] == {}
    assert c.get("/games/api/roulette/spin").status_code == 404          # unknown game
    api = c.get("/games/api").json()
    assert api["games"] == [] and not any(g in api for g in ("roulette", "craps", "russian", "trivia"))
    assert all("/roulette/" not in e for e in api["examples"])           # roulette examples only when roulette is there
    assert c.get("/games").status_code == 200 and c.get("/games/overlay").status_code == 200


def test_a_single_game_can_be_added_and_removed_live(real_world):
    real_world.installer.install("games")
    real_world.host.load_all()
    c = TestClient(real_world.app)
    real_world.installer.install("games_craps")
    assert real_world.host.load("games_craps"), real_world.host.errors
    assert registry(c) == ["craps"]
    assert c.get("/games/api/craps/validate?bet=pass&amount=10").json()["valid"]
    assert "craps" in c.get("/games/api").json()

    stopped, clean = asyncio.run(real_world.host.unload("games_craps"))
    assert stopped == ["games_craps"] and clean
    assert registry(c) == [] and "craps" not in c.get("/games/api/status").json()["games"]
    assert c.get("/games/api/craps/table").status_code == 404
    assert c.get("/games/api/craps/last").status_code == 404


def test_removing_the_host_removes_every_game_first(real_world):
    install_all(real_world)
    stopped, clean = asyncio.run(real_world.host.unload("games"))
    assert stopped[-1] == "games" and set(stopped[:-1]) == set(GAMES) and clean
    assert real_world.host.loaded == {}
    assert TestClient(real_world.app).get("/games/api/registry").status_code == 404


def test_bets_and_settings_survive_removing_and_reinstalling_a_game(real_world):
    install_all(real_world)
    c = TestClient(real_world.app)
    r = c.post("/games/api/craps/bet", json={"user": "amy", "bet": "pass", "amount": 25})
    assert r.status_code == 200 and r.json()["accepted"]
    c.post("/games/api/config", json={"craps": {"roll_seconds": 7}, "roulette": {"spin_seconds": 11}})
    asyncio.run(real_world.host.unload("games_craps"))

    # saving the config while craps is away must not throw its settings out of the file
    c.post("/games/api/config", json={"roulette": {"spin_seconds": 12}})
    saved = json.loads((real_world.config / "games.json").read_text())
    assert saved["craps"]["roll_seconds"] == 7 and saved["roulette"]["spin_seconds"] == 12

    assert real_world.host.load("games_craps"), real_world.host.errors
    assert c.get("/games/api/config").json()["config"]["craps"]["roll_seconds"] == 7
    table = c.get("/games/api/craps/table").json()["table"]
    assert [b["user"] for b in table["bets"]] == ["amy"]                  # the bet was still down
    led = c.get("/games/api/ledger").json()
    assert [e["game"] for e in led["events"]] == ["craps"] and led["events"][0]["type"] == "debit"


def test_ledger_recovery_replays_events_from_every_state_file(real_world):
    """A crash between 'save the table' and 'append to the ledger' leaves the event only in the
    table's journal; the host repairs it at start, whichever add-ons are installed."""
    cfg = real_world.config
    ev = lambda seq, game, user: {"seq": seq, "ts": 1.0, "game": game, "type": "debit", "user": user, "amount": 10,
                                  "reason": "bet", "bet_id": f"b-{seq}", "bet": "x", "roll_id": None}
    # roulette's file holds seq 1 (its own) and 2 (craps', still unwritten when it was saved)
    (cfg / "games_roulette_table.json").write_text(json.dumps(
        {"bets": [], "last_seq": 2, "journal": [ev(1, "roulette", "ann"), ev(2, "craps", "bo")]}))
    (cfg / "games_craps_table.json").write_text(json.dumps({"bets": [], "last_seq": 2, "journal": [ev(2, "craps", "bo")]}))
    (cfg / "games_russian.json").write_text(json.dumps({"game": None, "last_seq": 3, "journal": [ev(3, "russian", "cy")]}))
    (cfg / "games_trivia_bank.json").write_text(json.dumps({"pool": {}}))       # holds no coins: ignored
    real_world.installer.install("games")           # note: the game add-ons are NOT installed
    real_world.host.load_all()
    c = TestClient(real_world.app)
    led = c.get("/games/api/ledger").json()
    assert [(e["seq"], e["game"]) for e in led["events"]] == [(1, "roulette"), (2, "craps"), (3, "russian")]
    assert led["last_seq"] == 3
    lines = (cfg / "games_ledger.jsonl").read_text().strip().splitlines()
    assert [json.loads(x)["seq"] for x in lines] == [1, 2, 3]                   # each replayed exactly once


def test_api_index_lists_only_installed_games(real_world):
    install_all(real_world)
    c = TestClient(real_world.app)
    api = c.get("/games/api").json()
    assert api["games"] == ["roulette", "craps", "russian", "trivia"] or set(api["games"]) == {"roulette", "craps", "russian", "trivia"}
    for g in ("roulette", "craps", "russian", "trivia"):
        assert isinstance(api[g], dict) and api[g]
    assert set(api["appearance_keys"]) == {"roulette", "craps", "russian", "trivia"}
    assert any("roulette" in e for e in api["examples"])


def test_registry_change_is_announced_to_open_pages(real_world):
    real_world.installer.install("games")
    real_world.host.load_all()
    with TestClient(real_world.app) as c:
        with c.websocket_connect("/games/ws/panel") as ws:
            msgs = [ws.receive_json()]                                  # config
            real_world.installer.install("games_roulette")
            c.portal.call(lambda: real_world.host.load("games_roulette"))
            seen = []
            for _ in range(6):
                m = ws.receive_json()
                seen.append(m["type"])
                if m["type"] == "registry":
                    break
            assert "registry" in seen, seen
