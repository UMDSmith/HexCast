"""
HexCast - Ticker overlay module
================================

A Hexcast plugin (see plugin.py): install it from the + tab and it adds:
    http://localhost:4747/ticker          -> control panel (content + style + placement)
    http://localhost:4747/ticker/overlay  -> OBS browser source

A news-style scrolling ticker you place anywhere on a scaled 1920x1080 stage.
What scrolls is a list of named **feeds**, each a list of items:

  * The panel edits them by hand.
  * A bot writes them over HTTP - replace a whole feed, or add / upsert / remove
    single items (items with a `key` upsert, so `key=alice&value=1300` updates
    Alice's line in place - made for things like everyone's hexcoin balance).
  * `config/ticker_feeds.json` is watched: write it directly and the overlay
    follows within a second.
  * A feed can poll a JSON URL (list or {key: value} dict) on an interval.

Items can expire (`ttl` seconds), and a feed can give every new line a default
lifetime (its own `ttl`, e.g. 600 = each new line scrolls for 10 minutes). The overlay pulls the next item from the live
list as each one scrolls off, so updates land mid-scroll without a jump.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from hexcast_core import paths

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent          # this plugin's folder
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = paths.CONFIG_DIR
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "ticker.json"
FEEDS_PATH = CONFIG_DIR / "ticker_feeds.json"


def _read_static(name: str) -> str:
    path = STATIC_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Static file '{name}' not found at {path}. The Ticker module needs "
            f"ticker_panel.html and ticker_overlay.html in the plugin's static/ folder."
        )
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# style config
# --------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    # --- behaviour ---
    "visible": True,               # master show/hide (also /ticker/api/show|hide)
    "hide_when_empty": True,       # fade the bar out when nothing is left to scroll
    "speed": 120,                  # stage px per second
    "direction": "left",           # "left" (text moves right-to-left) | "right"
    "gap": 60,                     # stage px between items
    "separator": "•",         # drawn between items ("" = none)
    "separator_color": "#ff3b30",
    "show_labels": True,           # draw each feed's label as a badge before its items
    "label_bg": "#ff3b30",
    "label_color": "#ffffff",

    # --- fixed title pinned to the start edge (e.g. "HEXBANK") ---
    "title": "",
    "title_bg": "#ff3b30",
    "title_color": "#ffffff",

    # --- text ---
    "font_family": "Inter",
    "font_size": 40,
    "font_weight": 700,
    "letter_spacing": 0,
    "uppercase": False,
    "text_color": "#ffffff",
    "outline": False,
    "outline_color": "#000000",
    "outline_width": 2,
    "shadow": True,

    # --- bar ---
    "bg_style": "solid",           # none | solid | gradient | glass
    "bg_color": "#0b0b10",
    "bg_opacity": 0.75,
    "bg_color2": "#1a1a2e",
    "bg_gradient_angle": 90,
    "bg_blur": 8,
    "border_color": "#ff3b30",
    "border_width": 0,
    "radius": 12,
    "pad_x": 20,                   # inner left/right padding, stage px
    "fade_edges": 60,              # soft fade at both ends, stage px (0 = hard edge)
    "opacity": 1.0,                # whole-ticker transparency

    # --- placement (percentages on the 1920x1080 stage) ---
    # floats, so small drags aren't truncated to whole percents on save
    "box_x": 0.0, "box_y": 91.0, "box_w": 100.0, "box_h": 7.0,
}

_CHOICES = {
    "direction": ("left", "right"),
    "bg_style": ("none", "solid", "gradient", "glass"),
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _clean_config(cfg: dict) -> dict:
    """Keep only known keys, coerced to the default's type; drop anything else."""
    out: dict[str, Any] = {}
    for k, dv in DEFAULT_CONFIG.items():
        v = cfg.get(k, dv)
        try:
            if isinstance(dv, bool):
                v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(dv, int):
                v = int(float(v))
            elif isinstance(dv, float):
                v = float(v)
            else:
                v = "" if v is None else str(v)
        except (TypeError, ValueError):
            v = dv
        if k in _CHOICES and v not in _CHOICES[k]:
            v = dv
        out[k] = v
    out["speed"] = max(5, min(2000, out["speed"]))
    out["opacity"] = max(0.0, min(1.0, out["opacity"]))
    out["bg_opacity"] = max(0.0, min(1.0, out["bg_opacity"]))
    for k in ("box_x", "box_y"):
        out[k] = round(max(0.0, min(100.0, out[k])), 2)
    for k in ("box_w", "box_h"):
        out[k] = round(max(1.0, min(100.0, out[k])), 2)
    return out


def load_config() -> dict:
    raw = {}
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    return _clean_config(_deep_merge(DEFAULT_CONFIG, raw))


def save_config(cfg: dict) -> dict:
    clean = _clean_config(cfg)
    CONFIG_PATH.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return clean


CONFIG = load_config()


# --------------------------------------------------------------------------
# feeds
# --------------------------------------------------------------------------
# A feed: {"name", "label", "enabled", "color", "template", "sort", "limit",
#          "ttl" (seconds each new line lasts, 0 = forever),
#          "source": {...}, "items": [item, ...]}
# An item: {"id", "text", "key", "value", "color", "expires_at"}
#   - explicit `text` is shown as-is; otherwise the feed's `template` is filled
#     from {key} / {value} / {text} (default "{key}: {value}").
#   - items with a `key` are unique within their feed: writing the same key again
#     updates that item in place.

NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
MAX_ITEMS = 500
MAX_TEXT = 300
SORTS = ("none", "value_desc", "value_asc", "key", "newest")
ANNOUNCE = "announce"

DEFAULT_SOURCE: dict[str, Any] = {
    "url": "",            # http(s)://... or a path on this server ("/games/api/...")
    "interval": 30,       # seconds between polls (min 5)
    "path": "",           # dot path into the JSON to the list/dict ("data.users")
    "key_field": "",      # object lists: field for {key}   (auto: key/name/user/username)
    "value_field": "",    # object lists: field for {value} (auto: value/balance/amount/coins/score)
    "text_field": "",     # object lists: field used as the whole line (overrides the template)
}

FEEDS: list[dict] = []
_SOURCE_STATE: dict[str, dict] = {}   # feed name -> {"next_at", "last_ok", "last_error", "count"}
_FILE_MTIME: float | None = None      # mtime of our own last write / read
_LOADED = False                       # set after the first load_feeds()
_LOCK = asyncio.Lock()


def _now() -> float:
    return time.time()


def _norm_name(name: Any) -> str | None:
    n = str(name or "").strip().lower()
    return n if NAME_RE.match(n) else None


def _new_id() -> str:
    return secrets.token_hex(4)


def _num_or_str(v: Any) -> Any:
    """Keep numbers numeric (so sorting works) and everything else as short text."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    try:
        f = float(s.replace(",", ""))
        return int(f) if f.is_integer() and "." not in s else f
    except ValueError:
        return s[:MAX_TEXT]


def _norm_item(raw: Any) -> dict | None:
    if isinstance(raw, (str, int, float)) and not isinstance(raw, bool):
        raw = {"text": str(raw)}
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()[:MAX_TEXT]
    key = str(raw.get("key") or "").strip()[:80]
    value = _num_or_str(raw.get("value"))
    if not text and not key and value is None:
        return None
    exp = raw.get("expires_at")
    ttl = raw.get("ttl")
    forever = (exp is None and ttl is not None
               and str(ttl).strip().lower() in ("0", "0.0", "none", "never", "forever"))
    if exp is None and not forever and ttl not in (None, ""):
        try:
            exp = _now() + max(1.0, float(ttl))
        except (TypeError, ValueError):
            exp = None
    try:
        exp = float(exp) if exp is not None else None
    except (TypeError, ValueError):
        exp = None
    out = {
        "id": str(raw.get("id") or _new_id())[:16],
        "text": text,
        "key": key,
        "value": value,
        "color": str(raw.get("color") or "").strip()[:32],
        "expires_at": exp,
        "added_at": float(raw.get("added_at") or _now()),
    }
    if forever:
        out["_forever"] = True   # explicit ttl=0: skip the feed's default lifetime (popped before saving)
    return out


def _norm_source(raw: Any) -> dict:
    src = dict(DEFAULT_SOURCE)
    if isinstance(raw, dict):
        for k in DEFAULT_SOURCE:
            if k in raw:
                src[k] = raw[k]
    src["url"] = str(src["url"] or "").strip()
    try:
        src["interval"] = max(5, int(float(src["interval"])))
    except (TypeError, ValueError):
        src["interval"] = 30
    for k in ("path", "key_field", "value_field", "text_field"):
        src[k] = str(src[k] or "").strip()
    return src


def _norm_feed(raw: dict, name: str | None = None) -> dict | None:
    name = _norm_name(name or raw.get("name"))
    if not name:
        return None
    sort = str(raw.get("sort") or "none")
    try:
        limit = max(0, int(float(raw.get("limit") or 0)))
    except (TypeError, ValueError):
        limit = 0
    try:
        ttl = max(0, min(7 * 86400, int(float(raw.get("ttl") or 0))))
    except (TypeError, ValueError):
        ttl = 0
    items = []
    seen_keys: dict[str, int] = {}
    for it in (raw.get("items") or [])[:MAX_ITEMS]:
        n = _norm_item(it)
        if not n:
            continue
        if n["key"]:
            if n["key"] in seen_keys:            # last write wins for duplicate keys
                items[seen_keys[n["key"]]] = n
                continue
            seen_keys[n["key"]] = len(items)
        items.append(n)
    return {
        "name": name,
        "label": str(raw.get("label") or "").strip()[:60],
        "enabled": bool(raw.get("enabled", True)),
        "color": str(raw.get("color") or "").strip()[:32],
        "template": str(raw.get("template") or "{key}: {value}")[:200],
        "sort": sort if sort in SORTS else "none",
        "limit": limit,
        "ttl": ttl,
        "source": _norm_source(raw.get("source")),
        "items": items,
    }


def _stamp_lifetimes(new: dict, old: dict | None, stamp: bool = True) -> None:
    """After a bulk write (panel save, items/values post, URL poll, file edit): lines
    that were already in the feed keep their id and expiry; lines that are new get
    the feed's default lifetime (`ttl` seconds, 0 = forever) unless they carry their own."""
    prev_ids: dict[str, dict] = {}
    prev_keys: dict[str, dict] = {}
    for it in (old or {}).get("items") or []:
        prev_ids[it["id"]] = it
        if it["key"]:
            prev_keys[it["key"]] = it
    now = _now()
    used: set[str] = set()
    for it in new["items"]:
        forever = it.pop("_forever", False)
        prev = prev_ids.get(it["id"]) or (prev_keys.get(it["key"]) if it["key"] else None)
        if prev is not None and prev["id"] not in used:
            it["id"] = prev["id"]            # same line, so the overlay doesn't treat it as brand new
            if it["expires_at"] is None and not forever:
                it["expires_at"] = prev["expires_at"]
        elif stamp and it["expires_at"] is None and not forever and new.get("ttl"):
            it["expires_at"] = now + new["ttl"]
        used.add(it["id"])


def _feed(name: str) -> dict | None:
    for f in FEEDS:
        if f["name"] == name:
            return f
    return None


def _fmt_value(v: Any) -> str:
    if isinstance(v, bool) or v is None:
        return "" if v is None else str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v)


def _display(feed: dict, item: dict) -> str:
    if item["text"]:
        return item["text"]
    out = feed["template"] or "{key}: {value}"
    for k, v in (("key", item["key"]), ("value", _fmt_value(item["value"])),
                 ("text", item["text"]), ("label", feed["label"])):
        out = out.replace("{" + k + "}", v or "")
    return out.strip()[:MAX_TEXT]


def _sorted_items(feed: dict) -> list[dict]:
    items = list(feed["items"])
    s = feed["sort"]

    def num(it):
        v = it["value"]
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else float("-inf")

    if s == "value_desc":
        items.sort(key=num, reverse=True)
    elif s == "value_asc":
        items.sort(key=lambda it: num(it) if num(it) != float("-inf") else float("inf"))
    elif s == "key":
        items.sort(key=lambda it: (it["key"] or it["text"]).lower())
    elif s == "newest":
        items.sort(key=lambda it: it["added_at"], reverse=True)
    if feed["limit"]:
        items = items[: feed["limit"]]
    return items


def render_feeds() -> list[dict]:
    """What the overlay scrolls: enabled feeds with their display lines, in order."""
    out = []
    now = _now()
    for f in FEEDS:
        if not f["enabled"]:
            continue
        lines = []
        for it in _sorted_items(f):
            if it["expires_at"] is not None and it["expires_at"] <= now:
                continue
            text = _display(f, it)
            if text:
                lines.append({"id": it["id"], "text": text, "color": it["color"] or f["color"]})
        if lines:
            out.append({"name": f["name"], "label": f["label"], "color": f["color"], "items": lines})
    return out


def _public_feed(f: dict) -> dict:
    st = _SOURCE_STATE.get(f["name"]) or {}
    return {**f, "source_status": {k: st.get(k) for k in ("last_ok", "last_error", "count")}
            if f["source"]["url"] else None,
            "rendered": [_display(f, it) for it in _sorted_items(f)]}


def load_feeds() -> bool:
    """(Re)read the feeds file. Returns False (keeping the current feeds) if it's unreadable."""
    global FEEDS, _FILE_MTIME, _LOADED
    raw: Any = {}
    if FEEDS_PATH.exists():
        try:
            raw = json.loads(FEEDS_PATH.read_text(encoding="utf-8"))
            _FILE_MTIME = FEEDS_PATH.stat().st_mtime
        except Exception as exc:
            print(f"[ticker] could not read {FEEDS_PATH.name}: {exc} - keeping current feeds", flush=True)
            try:
                _FILE_MTIME = FEEDS_PATH.stat().st_mtime   # don't re-warn every second
            except OSError:
                pass
            return False
    else:
        raw = {"feeds": [{"name": "news", "label": "", "items": [
            "Welcome to the stream!",
            "Edit this text in the Ticker tab - or let the bot write it over the API",
        ]}]}
    lst = raw.get("feeds") if isinstance(raw, dict) else raw
    feeds, seen = [], set()
    for f in lst or []:
        if not isinstance(f, dict):
            continue
        n = _norm_feed(f)
        if n and n["name"] not in seen:
            seen.add(n["name"])
            # on an external edit, lines the bot just wrote into the file get the
            # feed's lifetime; on startup nothing is new, so nothing is stamped
            _stamp_lifetimes(n, _feed(n["name"]), stamp=_LOADED)
            feeds.append(n)
    FEEDS = feeds
    _LOADED = True
    return True


def save_feeds() -> None:
    """Atomic write, remembering the mtime so the watcher ignores our own write."""
    global _FILE_MTIME
    tmp = FEEDS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"feeds": FEEDS}, indent=2), encoding="utf-8")
    os.replace(tmp, FEEDS_PATH)
    try:
        _FILE_MTIME = FEEDS_PATH.stat().st_mtime
    except OSError:
        _FILE_MTIME = None


load_feeds()


# --------------------------------------------------------------------------
# websocket hub
# --------------------------------------------------------------------------

class Hub:
    def __init__(self) -> None:
        self.overlay: set[WebSocket] = set()
        self.preview: set[WebSocket] = set()   # the panel's embedded preview (not counted)
        self.panel: set[WebSocket] = set()

    def overlay_count(self) -> int:
        return len(self.overlay - self.preview)

    async def _send(self, group: set[WebSocket], payload: dict) -> None:
        dead = []
        text = json.dumps(payload)
        for ws in list(group):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            group.discard(ws)

    async def broadcast_config(self) -> None:
        msg = {"type": "config", "config": CONFIG}
        await self._send(self.overlay, msg)
        await self._send(self.panel, msg)

    async def broadcast_feeds(self) -> None:
        await self._send(self.overlay, {"type": "feeds", "feeds": render_feeds()})
        await self._send(self.panel, {"type": "feeds", "feeds": [_public_feed(f) for f in FEEDS]})


HUB = Hub()


async def _changed() -> None:
    save_feeds()
    await HUB.broadcast_feeds()


# --------------------------------------------------------------------------
# background: expiry sweep, file watch, URL sources
# --------------------------------------------------------------------------

_PORT: int = 4747
_APP = None
_STARTED = False


def _client(url: str) -> httpx.AsyncClient:
    # Paths on this server are called in-process (a loopback socket can be
    # hijacked by port forwards - see countdown.py).
    if url.startswith("/") and _APP is not None:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP),
                                 base_url="http://hexcast.internal", timeout=10)
    return httpx.AsyncClient(timeout=10, follow_redirects=True)


def _dig(data: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p]:
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list):
            try:
                data = data[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return data


def _pick(obj: dict, field: str, auto: tuple[str, ...]) -> Any:
    if field:
        return _dig(obj, field)
    for k in auto:
        if k in obj:
            return obj[k]
    return None


def items_from_payload(data: Any, src: dict) -> list[dict]:
    """Turn a polled JSON (or text) payload into raw items."""
    data = _dig(data, src.get("path", "")) if src.get("path") else data
    out: list[dict] = []
    if isinstance(data, str):
        out = [{"text": ln.strip()} for ln in data.splitlines() if ln.strip()]
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                val = _pick(v, src.get("value_field", ""), ("value", "balance", "amount", "coins", "score"))
                txt = _dig(v, src["text_field"]) if src.get("text_field") else None
                out.append({"key": str(k), "value": val, "text": txt or ""})
            else:
                out.append({"key": str(k), "value": v})
    elif isinstance(data, list):
        for el in data:
            if isinstance(el, dict):
                key = _pick(el, src.get("key_field", ""), ("key", "name", "user", "username", "login"))
                val = _pick(el, src.get("value_field", ""), ("value", "balance", "amount", "coins", "score"))
                txt = _dig(el, src["text_field"]) if src.get("text_field") else el.get("text")
                out.append({"key": "" if key is None else str(key), "value": val, "text": txt or ""})
            elif el is not None:
                out.append({"text": str(el)})
    return out[:MAX_ITEMS]


async def poll_source(feed: dict) -> None:
    name, src = feed["name"], feed["source"]
    st = _SOURCE_STATE.setdefault(name, {})
    st["next_at"] = _now() + src["interval"]
    try:
        async with _client(src["url"]) as c:
            r = await c.get(src["url"])
            r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            data = r.text
        raw = items_from_payload(data, src)
    except Exception as exc:
        st["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        await HUB._send(HUB.panel, {"type": "feeds", "feeds": [_public_feed(f) for f in FEEDS]})
        return
    async with _LOCK:
        f = _feed(name)
        if not f or f["source"]["url"] != src["url"]:
            return
        fresh = _norm_feed({"name": name, "ttl": f["ttl"], "items": raw})
        _stamp_lifetimes(fresh, f)
        f["items"] = fresh["items"]
        st.update(last_ok=_now(), last_error=None, count=len(f["items"]))
        await _changed()


async def _background() -> None:
    while True:
        try:
            await asyncio.sleep(1.0)
            # 1) the feeds file was edited by something else -> reload
            try:
                mtime = FEEDS_PATH.stat().st_mtime if FEEDS_PATH.exists() else None
            except OSError:
                mtime = None
            if mtime is not None and mtime != _FILE_MTIME:
                async with _LOCK:
                    reloaded = load_feeds()
                if reloaded:
                    print(f"[ticker] {FEEDS_PATH.name} changed on disk - reloaded", flush=True)
                    await HUB.broadcast_feeds()
            # 2) drop expired items
            now = _now()
            expired = False
            async with _LOCK:
                for f in FEEDS:
                    keep = [it for it in f["items"] if it["expires_at"] is None or it["expires_at"] > now]
                    if len(keep) != len(f["items"]):
                        f["items"] = keep
                        expired = True
                if expired:
                    await _changed()
            # 3) poll URL sources that are due
            for f in list(FEEDS):
                if f["enabled"] and f["source"]["url"]:
                    st = _SOURCE_STATE.setdefault(f["name"], {})
                    if now >= st.get("next_at", 0):
                        st["next_at"] = now + f["source"]["interval"]
                        asyncio.create_task(poll_source(f))
        except asyncio.CancelledError:
            raise
        except Exception as exc:   # never let the loop die
            print(f"[ticker] background error: {exc}", flush=True)


def ensure_started() -> None:
    """hexcast.py uses FastAPI(lifespan=...), which makes Starlette ignore
    add_event_handler("startup") - so the loop starts on the first request."""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    asyncio.get_running_loop().create_task(_background())


# --------------------------------------------------------------------------
# request helpers
# --------------------------------------------------------------------------

async def _params(request: Request) -> dict:
    """Query string merged with a JSON (or form) body; the body wins. Lets a bot
    use plain GETs (`/ticker/api/say?text=hi`) or POST JSON interchangeably."""
    out: dict[str, Any] = dict(request.query_params)
    if request.method in ("POST", "PUT", "PATCH"):
        ctype = request.headers.get("content-type", "")
        try:
            if "application/json" in ctype:
                body = await request.json()
                if isinstance(body, dict):
                    out.update(body)
                elif isinstance(body, list):
                    out["items"] = body
            elif "form" in ctype:
                form = await request.form()
                out.update({k: v for k, v in form.items()})
            else:
                raw = (await request.body()).decode("utf-8", "replace").strip()
                if raw:
                    try:
                        body = json.loads(raw)
                        if isinstance(body, dict):
                            out.update(body)
                        elif isinstance(body, list):
                            out["items"] = body
                    except ValueError:
                        out.setdefault("text", raw)
        except Exception:
            pass
    return out


def _truthy(v: Any, default: bool = True) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _err(msg: str, code: int = 400, **extra) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg, **extra}, status_code=code)


def _bad_name(name: str) -> JSONResponse:
    return _err("bad_feed_name", 400,
                detail="feed names are 1-40 chars of a-z, 0-9, _ or -", name=name)


FEED_FIELDS = ("label", "enabled", "color", "template", "sort", "limit", "ttl", "source")


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/ticker", tags=["ticker"])
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    ensure_started()
    return HTMLResponse(_read_static("ticker_panel.html"), headers=_NOCACHE)


@router.get("/overlay", response_class=HTMLResponse)
async def overlay():
    ensure_started()
    return HTMLResponse(_read_static("ticker_overlay.html"), headers=_NOCACHE)


@router.get("/api/status")
async def api_status():
    ensure_started()
    rendered = render_feeds()
    return {
        "connected": True,
        "overlays": HUB.overlay_count(),
        "visible": CONFIG["visible"],
        "feeds": len(FEEDS),
        "items": sum(len(f["items"]) for f in rendered),
        "scrolling": [f["name"] for f in rendered],
    }


@router.get("/api/config")
async def api_get_config():
    ensure_started()
    return CONFIG


@router.post("/api/config")
async def api_set_config(request: Request):
    global CONFIG
    ensure_started()
    incoming = await request.json()
    if not isinstance(incoming, dict):
        return _err("expected a JSON object")
    CONFIG = save_config({**CONFIG, **incoming})
    await HUB.broadcast_config()
    return {"ok": True, "config": CONFIG}


async def _set_visible(on: bool) -> dict:
    global CONFIG
    CONFIG = save_config({**CONFIG, "visible": on})
    await HUB.broadcast_config()
    return {"ok": True, "visible": on}


@router.api_route("/api/show", methods=["GET", "POST"])
async def api_show():
    ensure_started()
    return await _set_visible(True)


@router.api_route("/api/hide", methods=["GET", "POST"])
async def api_hide():
    ensure_started()
    return await _set_visible(False)


@router.get("/api/items")
async def api_items():
    """Exactly what is scrolling right now (enabled feeds, sorted, limited, unexpired)."""
    ensure_started()
    return {"ok": True, "visible": CONFIG["visible"], "feeds": render_feeds()}


@router.get("/api/feeds")
async def api_feeds():
    ensure_started()
    return {"ok": True, "feeds": [_public_feed(f) for f in FEEDS]}


@router.post("/api/feeds")
async def api_replace_feeds(request: Request):
    """Replace every feed at once (the panel's Save). Body: {"feeds": [...]}."""
    global FEEDS
    ensure_started()
    body = await request.json()
    lst = body.get("feeds") if isinstance(body, dict) else body
    if not isinstance(lst, list):
        return _err("expected {\"feeds\": [...]}")
    out, seen = [], set()
    for raw in lst:
        if not isinstance(raw, dict):
            continue
        n = _norm_feed(raw)
        if n is None:
            return _bad_name(str(raw.get("name")))
        if n["name"] in seen:
            return _err("duplicate_feed", name=n["name"])
        seen.add(n["name"])
        out.append(n)
    async with _LOCK:
        for n in out:
            _stamp_lifetimes(n, _feed(n["name"]))
        FEEDS = out
        for name in list(_SOURCE_STATE):
            if not _feed(name):
                _SOURCE_STATE.pop(name, None)
        await _changed()
    return {"ok": True, "feeds": [_public_feed(f) for f in FEEDS]}


@router.post("/api/feeds/order")
async def api_order(request: Request):
    """Reorder feeds: {"order": ["announce", "balances", ...]} (unlisted keep their place at the end)."""
    global FEEDS
    ensure_started()
    p = await _params(request)
    order = p.get("order") or []
    if isinstance(order, str):
        order = [s for s in order.split(",") if s.strip()]
    rank = {str(n).strip().lower(): i for i, n in enumerate(order)}
    async with _LOCK:
        FEEDS = sorted(FEEDS, key=lambda f: rank.get(f["name"], len(rank) + FEEDS.index(f)))
        await _changed()
    return {"ok": True, "order": [f["name"] for f in FEEDS]}


@router.get("/api/feed/{name}")
async def api_get_feed(name: str):
    ensure_started()
    n = _norm_name(name)
    f = _feed(n) if n else None
    if not f:
        return _err("no_such_feed", 404, name=name)
    return {"ok": True, "feed": _public_feed(f)}


@router.api_route("/api/feed/{name}", methods=["POST", "PUT"])
async def api_put_feed(name: str, request: Request):
    """Create or update a feed. Settings present in the body are changed, others kept.
    `items` (list of strings/objects) REPLACES the items; `values` ({key: value})
    replaces them with keyed items - e.g. every user's balance in one call."""
    ensure_started()
    n = _norm_name(name)
    if not n:
        return _bad_name(name)
    p = await _params(request)
    async with _LOCK:
        f = _feed(n)
        base = dict(f) if f else {"name": n}
        for k in FEED_FIELDS:
            if k in p:
                if k == "source":
                    if isinstance(p[k], dict):
                        base[k] = {**(base.get("source") or {}), **p[k]}
                    elif not p[k]:
                        base[k] = {}                     # "source": null / "" clears it
                else:
                    base[k] = p[k]
        if "enabled" in p:
            base["enabled"] = _truthy(p["enabled"])
        if isinstance(p.get("values"), dict):
            base["items"] = [{"key": str(k), "value": v} for k, v in p["values"].items()]
        elif "items" in p:
            base["items"] = p["items"] if isinstance(p["items"], list) else [p["items"]]
        elif "text" in p and "items" not in base:
            base["items"] = [p["text"]]
        new = _norm_feed(base, n)
        _stamp_lifetimes(new, f)
        if f:
            FEEDS[FEEDS.index(f)] = new
        else:
            if n == ANNOUNCE:
                FEEDS.insert(0, new)
            else:
                FEEDS.append(new)
        if new["source"]["url"]:
            _SOURCE_STATE.setdefault(n, {})["next_at"] = 0   # poll right away
        await _changed()
    return {"ok": True, "created": f is None, "feed": _public_feed(new)}


async def _add(name: str, p: dict, default_ttl: float | None = None,
               label: str | None = None) -> JSONResponse | dict:
    n = _norm_name(name)
    if not n:
        return _bad_name(name)
    item = _norm_item(p)
    if not item:
        return _err("empty_item", detail="send text, or key and/or value")
    forever = item.pop("_forever", False)
    async with _LOCK:
        f = _feed(n)
        created = f is None
        if created:
            f = _norm_feed({"name": n, "label": label or ""}, n)
            if n == ANNOUNCE:
                FEEDS.insert(0, f)
            else:
                FEEDS.append(f)
        # no ttl given: use the feed's line lifetime (then the route's default)
        life = f["ttl"] or default_ttl
        if item["expires_at"] is None and not forever and life:
            item["expires_at"] = _now() + life
        updated = False
        if item["key"]:
            for i, old in enumerate(f["items"]):
                if old["key"] == item["key"]:
                    item["id"] = old["id"]
                    item["added_at"] = old["added_at"]
                    if not item["text"] and "text" not in p:
                        item["text"] = old["text"]
                    if not item["color"] and "color" not in p:
                        item["color"] = old["color"]
                    if item["value"] is None and "value" not in p:
                        item["value"] = old["value"]
                    f["items"][i] = item
                    updated = True
                    break
        if not updated:
            if len(f["items"]) >= MAX_ITEMS:
                f["items"].pop(0)                       # drop the oldest
            if _truthy(p.get("first"), False):
                f["items"].insert(0, item)
            else:
                f["items"].append(item)
        await _changed()
    return {"ok": True, "feed": n, "created_feed": created, "updated": updated,
            "item": {**item, "display": _display(f, item)}}


@router.api_route("/api/feed/{name}/add", methods=["GET", "POST"])
async def api_add(name: str, request: Request):
    """Add one item - or, with `key`, upsert it (same key updates in place).
    Params: text, key, value, color, ttl (seconds; default = the feed's line
    lifetime, 0 = keep forever), first (1 = put at the front)."""
    ensure_started()
    return await _add(name, await _params(request))


@router.api_route("/api/feed/{name}/set", methods=["GET", "POST"])
async def api_set(name: str, request: Request):
    """Alias of /add - reads naturally for keyed upserts: /set?key=alice&value=1300."""
    ensure_started()
    return await _add(name, await _params(request))


@router.api_route("/api/feed/{name}/remove", methods=["GET", "POST"])
async def api_remove(name: str, request: Request):
    """Remove items by `id`, `key` or exact `text` (any that match)."""
    ensure_started()
    n = _norm_name(name)
    f = _feed(n) if n else None
    if not f:
        return _err("no_such_feed", 404, name=name)
    p = await _params(request)
    ids = {str(p["id"])} if p.get("id") else set()
    key = str(p.get("key") or "").strip()
    text = str(p.get("text") or "").strip()
    if not (ids or key or text):
        return _err("nothing_to_match", detail="pass id, key or text")
    async with _LOCK:
        before = len(f["items"])
        f["items"] = [it for it in f["items"]
                      if not (it["id"] in ids or (key and it["key"] == key)
                              or (text and it["text"] == text))]
        removed = before - len(f["items"])
        if removed:
            await _changed()
    return {"ok": True, "removed": removed}


@router.api_route("/api/feed/{name}/clear", methods=["GET", "POST"])
async def api_clear(name: str):
    ensure_started()
    n = _norm_name(name)
    f = _feed(n) if n else None
    if not f:
        return _err("no_such_feed", 404, name=name)
    async with _LOCK:
        removed = len(f["items"])
        f["items"] = []
        await _changed()
    return {"ok": True, "removed": removed}


@router.api_route("/api/feed/{name}/enable", methods=["GET", "POST"])
async def api_enable(name: str, request: Request):
    """Turn a feed on/off without losing its items: ?on=1 | ?on=0."""
    ensure_started()
    n = _norm_name(name)
    f = _feed(n) if n else None
    if not f:
        return _err("no_such_feed", 404, name=name)
    p = await _params(request)
    async with _LOCK:
        f["enabled"] = _truthy(p.get("on", p.get("enabled")))
        await _changed()
    return {"ok": True, "enabled": f["enabled"]}


@router.api_route("/api/feed/{name}/refresh", methods=["GET", "POST"])
async def api_refresh(name: str):
    """Poll a feed's URL source now."""
    ensure_started()
    n = _norm_name(name)
    f = _feed(n) if n else None
    if not f:
        return _err("no_such_feed", 404, name=name)
    if not f["source"]["url"]:
        return _err("no_source", detail="this feed has no source.url")
    await poll_source(f)
    st = _SOURCE_STATE.get(n) or {}
    return {"ok": not st.get("last_error"), "error": st.get("last_error"),
            "count": len(f["items"]), "feed": _public_feed(f)}


@router.api_route("/api/feed/{name}/delete", methods=["GET", "POST", "DELETE"])
async def api_delete(name: str):
    global FEEDS
    ensure_started()
    n = _norm_name(name)
    f = _feed(n) if n else None
    if not f:
        return _err("no_such_feed", 404, name=name)
    async with _LOCK:
        FEEDS = [x for x in FEEDS if x["name"] != n]
        _SOURCE_STATE.pop(n, None)
        await _changed()
    return {"ok": True, "deleted": n}


@router.api_route("/api/say", methods=["GET", "POST"])
async def api_say(request: Request):
    """One-off announcement: adds to the `announce` feed (first in line) and expires
    after `ttl` seconds (default: the announce feed's line lifetime, else 60;
    ttl=0 keeps it until removed)."""
    ensure_started()
    p = await _params(request)
    if not str(p.get("text") or "").strip():
        return _err("empty_item", detail="pass text")
    p.pop("key", None)
    return await _add(ANNOUNCE, p, default_ttl=60, label="")


@router.websocket("/ws/overlay")
async def ws_overlay(ws: WebSocket):
    await ws.accept()
    ensure_started()
    HUB.overlay.add(ws)
    if ws.query_params.get("preview") == "1":
        HUB.preview.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        await ws.send_text(json.dumps({"type": "feeds", "feeds": render_feeds()}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.overlay.discard(ws)
        HUB.preview.discard(ws)


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    ensure_started()
    HUB.panel.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        await ws.send_text(json.dumps({"type": "feeds", "feeds": [_public_feed(f) for f in FEEDS]}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.panel.discard(ws)


# --------------------------------------------------------------------------
# attach
# --------------------------------------------------------------------------

def attach_ticker(app, port: int = 4747) -> None:
    """Mount the Ticker routes onto an existing FastAPI app."""
    global _PORT, _APP
    _PORT = port
    _APP = app
    app.include_router(router)
    print(f"  Ticker panel:        http://localhost:{port}/ticker", flush=True)
    print(f"  Ticker source:       http://localhost:{port}/ticker/overlay", flush=True)
