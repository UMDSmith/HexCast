"""
HexCast - Clips module
======================

Drop this next to hexcast.py, then add two lines after the /media mount:

    from clips import attach_clips
    attach_clips(app, PORT)

Adds:
    http://localhost:4747/clips          -> control panel (queue + transport)
    http://localhost:4747/clips/overlay  -> clip player (OBS browser source)

The streamer queues Twitch clip/VOD links (paste one URL or a whole blob of
chat spam - every twitch link in it is extracted), then fires them one at a
time at a full-window overlay. Nothing auto-advances: every clip is played
deliberately, from the panel or from a bot via GET /clips/api/play/{num}.

Playback is direct media, not the Twitch iframe: yt-dlp resolves clips to
their MP4 (optionally pre-downloaded to media/clips/ so playback is instant
and immune to URL expiry) and VODs to HLS played through hls.js. If
resolution fails (sub-only VOD, expired link), the Twitch iframe embed is
used as a best-effort fallback.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import random
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import WebSocket, WebSocketDisconnect

# --------------------------------------------------------------------------
# paths / constants
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = Path(os.environ.get("HEXCAST_CONFIG_DIR", BASE_DIR / "config"))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

STORE_PATH = CONFIG_DIR / "clips.json"

# Same env var hexcast.py uses, so cached clips land under the mounted /media
# root and get served by the existing StaticFiles mount.
MEDIA_ROOT = Path(os.getenv("SOUNDBOARD_MEDIA_DIR", str(BASE_DIR / "media"))).expanduser().resolve()
CLIPS_MEDIA_DIR = MEDIA_ROOT / "clips"

# Twitch link shapes we accept. The (?!embed\b) keeps a pasted embed URL from
# being read as a clip whose slug is "embed".
CLIP_PATTERNS = (
    re.compile(r"https?://clips\.twitch\.tv/(?!embed\b)([A-Za-z0-9][\w-]*)"),
    re.compile(r"https?://(?:www\.|m\.)?twitch\.tv/[A-Za-z0-9_]+/clip/([A-Za-z0-9][\w-]*)"),
)
VOD_PATTERN = re.compile(r"https?://(?:www\.|m\.)?twitch\.tv/videos/(\d+)(\?[^\s\"'<>]*)?")
# YouTube: watch links (query parsed for v= and t=), short links, shorts, live.
YT_WATCH_PATTERN = re.compile(
    r"https?://(?:www\.|m\.|music\.)?youtube\.com/watch\?([^\s\"'<>]+)")
YT_ID_PATTERNS = (
    re.compile(r"https?://youtu\.be/([A-Za-z0-9_-]{6,20})(\?[^\s\"'<>]*)?"),
    re.compile(r"https?://(?:www\.|m\.)?youtube\.com/(?:shorts|live)/([A-Za-z0-9_-]{6,20})(\?[^\s\"'<>]*)?"),
)
YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
# Anything else: when the pasted text is a single bare URL (or a bot passes
# ?url=), it's accepted as generic media and handed to yt-dlp, which speaks
# most streaming sites. Blobs only auto-extract Twitch/YouTube links so a
# pasted chat log doesn't queue every random link in it.
GENERIC_URL = re.compile(r"https?://[^\s\"'<>]+")


def _read_static(name: str) -> str:
    path = STATIC_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Static file '{name}' not found at {path}. The Clips module needs "
            f"clips_panel.html and clips_overlay.html in ./static/ next to hexcast.py."
        )
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# persistence - settings, counter and queue live together in config/clips.json
# --------------------------------------------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    # Off by default: everything streams straight from the source at play
    # time. Turning this on downloads clips/videos to media/clips/ on add,
    # for instant starts that survive expiring media URLs. VODs always stream.
    "predownload": False,
    # If direct resolution fails, fall back to the site's iframe embed
    # (Twitch and YouTube only).
    "iframe_fallback": True,
    # Master volume applied by the overlay (0.0 - 1.0).
    "volume": 1.0,
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_store() -> dict:
    raw = {}
    if STORE_PATH.exists():
        try:
            raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    return {
        "settings": _deep_merge(DEFAULT_SETTINGS, raw.get("settings") or {}),
        "seq": int(raw.get("seq") or 0),
        "queue": [e for e in (raw.get("queue") or [])
                  if isinstance(e, dict) and e.get("id")],
    }


def save_store() -> None:
    STORE_PATH.write_text(json.dumps(STORE, indent=2), encoding="utf-8")


STORE = load_store()


def settings() -> dict:
    return STORE["settings"]


# --------------------------------------------------------------------------
# runtime state
# --------------------------------------------------------------------------

class State:
    def __init__(self) -> None:
        self.started = False
        self.ytdlp_version = ""
        self.log: list[str] = []

    def note(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.log.append(line)
        del self.log[:-60]
        print(f"[clips] {msg}", flush=True)


STATE = State()

# The overlay is the actual player; this is the server's source of truth,
# updated by overlay heartbeats. state: idle | loading | playing | paused.
PLAYER: dict[str, Any] = {"state": "idle", "item_id": "", "mode": "",
                          "position": 0.0, "duration": 0.0}


def _ytdlp_cmd() -> list[str] | None:
    """yt-dlp does Twitch (clips + VODs), not just YouTube - it's how a
    twitch.tv link becomes a direct MP4/HLS URL. Prefer the copy installed in
    this Python environment, but fall back to a yt-dlp already on PATH so an
    existing system install works too."""
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return None


def has_ytdlp() -> bool:
    return _ytdlp_cmd() is not None


def cache_path(entry: dict) -> Path:
    return CLIPS_MEDIA_DIR / f"{entry['id']}.mp4"


def public_entry(entry: dict) -> dict:
    out = dict(entry)
    p = cache_path(entry)
    if p.exists():
        try:
            out["cached"] = True
            out["cached_url"] = f"/media/clips/{entry['id']}.mp4?v={int(p.stat().st_mtime)}"
        except OSError:
            out["cached"] = False
    else:
        out["cached"] = False
    return out


def player_snapshot() -> dict:
    entry = entry_by_id(PLAYER["item_id"])
    return {
        "state": PLAYER["state"],
        "mode": PLAYER["mode"],
        "position": PLAYER["position"],
        "duration": PLAYER["duration"],
        "item": public_entry(entry) if entry else None,
    }


def status_snapshot() -> dict:
    q = STORE["queue"]
    return {
        "ok": True,
        "ytdlp": has_ytdlp(),
        "ytdlp_version": STATE.ytdlp_version,
        "player": player_snapshot(),
        "queued": sum(1 for e in q if e.get("status") == "queued"),
        "played": sum(1 for e in q if e.get("status") == "played"),
        "total": len(q),
        "log": STATE.log[-25:],
    }


# --------------------------------------------------------------------------
# broadcast hub
# --------------------------------------------------------------------------

class Hub:
    def __init__(self) -> None:
        self.overlay: set[WebSocket] = set()
        self.panel: set[WebSocket] = set()

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

    async def to_overlay(self, payload: dict) -> None:
        await self._send(self.overlay, payload)

    async def to_panel(self, payload: dict) -> None:
        await self._send(self.panel, payload)

    async def broadcast_config(self) -> None:
        payload = {"type": "config", "config": settings()}
        await self.to_overlay(payload)
        await self.to_panel(payload)

    async def broadcast_queue(self) -> None:
        await self.to_panel({"type": "queue",
                             "queue": [public_entry(e) for e in STORE["queue"]]})

    async def broadcast_player(self) -> None:
        await self.to_panel({"type": "player", "player": player_snapshot()})


HUB = Hub()


# --------------------------------------------------------------------------
# link extraction
# --------------------------------------------------------------------------

def parse_ts(raw: str) -> float:
    """Twitch timestamps: plain seconds ("5400") or "1h30m5s" in any subset."""
    raw = (raw or "").strip()
    if not raw:
        return 0.0
    if re.fullmatch(r"\d+", raw):
        return float(raw)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", raw)
    if not m or not any(m.groups()):
        return 0.0
    h, mn, s = (int(g or 0) for g in m.groups())
    return float(h * 3600 + mn * 60 + s)


def fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}h{(s % 3600) // 60}m{s % 60}s"


def _yt_link(pos: int, vid: str, query: str) -> tuple[int, dict]:
    start = parse_ts((parse_qs(query or "").get("t") or [""])[0])
    url = f"https://www.youtube.com/watch?v={vid}"
    if start:
        url += f"&t={int(start)}s"
    return (pos, {"kind": "youtube", "slug": vid, "start": start, "url": url})


def link_key(link: dict) -> tuple:
    """Dedupe identity. Generic media has no meaningful slug, so the URL is it."""
    ref = link["url"] if link["kind"] == "media" else link["slug"]
    return (link["kind"], ref, link.get("start") or 0.0)


def extract_links(text: str) -> list[dict]:
    """Pull every Twitch clip/VOD and YouTube link out of an arbitrary blob of
    text, in the order they appear, deduped within the blob. If the blob is
    nothing but a single URL of some other kind, that URL is accepted as
    generic media - yt-dlp speaks most streaming sites."""
    found: list[tuple[int, dict]] = []
    for pat in CLIP_PATTERNS:
        for m in pat.finditer(text):
            slug = m.group(1)
            found.append((m.start(), {
                "kind": "clip", "slug": slug, "start": 0.0,
                "url": f"https://clips.twitch.tv/{slug}",
            }))
    for m in VOD_PATTERN.finditer(text):
        vid = m.group(1)
        start = 0.0
        query = (m.group(2) or "").lstrip("?")
        if query:
            t = (parse_qs(query).get("t") or [""])[0]
            start = parse_ts(t)
        url = f"https://www.twitch.tv/videos/{vid}"
        if start:
            url += f"?t={fmt_ts(start)}"
        found.append((m.start(), {
            "kind": "vod", "slug": vid, "start": start, "url": url,
        }))
    for m in YT_WATCH_PATTERN.finditer(text):
        vid = (parse_qs(m.group(1)).get("v") or [""])[0]
        if YT_ID_RE.match(vid):
            found.append(_yt_link(m.start(), vid, m.group(1)))
    for pat in YT_ID_PATTERNS:
        for m in pat.finditer(text):
            found.append(_yt_link(m.start(), m.group(1), (m.group(2) or "").lstrip("?")))

    if not found:
        single = text.strip()
        if GENERIC_URL.fullmatch(single):
            found.append((0, {"kind": "media", "slug": urlparse(single).netloc or "media",
                              "start": 0.0, "url": single}))

    found.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _, link in found:
        key = link_key(link)
        if key not in seen:
            seen.add(key)
            out.append(link)
    return out


# --------------------------------------------------------------------------
# queue helpers
# --------------------------------------------------------------------------

# Shoutout clips play through the overlay without ever touching the queue.
EPHEMERAL: dict[str, dict] = {}
# Remaining shoutout clips, chained automatically as each one ends. Cleared
# by Stop and by any manual play.
SHOUTOUT_PENDING: list[dict] = []


def entry_by_id(eid: str) -> dict | None:
    for e in STORE["queue"]:
        if e["id"] == eid:
            return e
    return EPHEMERAL.get(eid)


def entry_by_ref(ref: str) -> dict | None:
    """Bots reference items by num, id, or exact clip slug; "next" is the
    first still-queued item."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.lower() == "next":
        for e in STORE["queue"]:
            if e.get("status") == "queued":
                return e
        return None
    e = entry_by_id(ref)
    if e:
        return e
    if ref.isdigit():
        for e in STORE["queue"]:
            if e.get("num") == int(ref):
                return e
    for e in STORE["queue"]:
        if e.get("slug") == ref:
            return e
    return None


def add_links(links: list[dict], source: str) -> tuple[list[dict], int]:
    """Append new entries for the given links; dedupe against the queue."""
    existing = {link_key(e) for e in STORE["queue"]}
    added = []
    for link in links:
        key = link_key(link)
        if key in existing:
            continue
        existing.add(key)
        STORE["seq"] += 1
        entry = {
            "id": secrets.token_hex(4),
            "num": STORE["seq"],
            "url": link["url"],
            "kind": link["kind"],
            "slug": link["slug"],
            "start": link["start"],
            "title": "",
            "duration": None,
            "thumbnail": "",
            "status": "queued",
            "error": "",
            "added_ts": time.time(),
            "source": source,
        }
        STORE["queue"].append(entry)
        added.append(entry)
    if added:
        save_store()
    return added, len(links) - len(added)


# --------------------------------------------------------------------------
# yt-dlp - one consistent mechanism: subprocess `python -m yt_dlp`, JSON via -j
# --------------------------------------------------------------------------

async def _ytdlp(*args: str, timeout: float = 120) -> bytes:
    cmd = _ytdlp_cmd()
    if cmd is None:
        raise RuntimeError("yt-dlp not found - install it in this environment "
                           "(pip install yt-dlp) or put it on PATH")
    proc = await asyncio.create_subprocess_exec(
        *cmd, "--no-warnings", "--no-playlist", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("yt-dlp timed out")
    if proc.returncode != 0:
        lines = (err or b"").decode("utf-8", errors="replace").strip().splitlines()
        detail = next((ln for ln in reversed(lines) if "ERROR" in ln), None) \
            or (lines[-1] if lines else f"yt-dlp exited {proc.returncode}")
        raise RuntimeError(detail.replace("ERROR: ", ""))
    return out


async def resolve_info(url: str) -> dict:
    return json.loads(await _ytdlp("-j", url, timeout=60))


def _pick_mp4(info: dict) -> str:
    for f in reversed(info.get("formats") or []):
        if f.get("ext") == "mp4" and str(f.get("protocol") or "").startswith("http") \
                and "m3u8" not in str(f.get("protocol")):
            if f.get("url"):
                return f["url"]
    return info.get("url") or ""


def _pick_hls(info: dict) -> str:
    for f in reversed(info.get("formats") or []):
        if "m3u8" in str(f.get("protocol") or "") and f.get("url"):
            return f["url"]
    url = info.get("url") or ""
    return url if ".m3u8" in url else ""


def _pick_direct(info: dict) -> tuple[str, str]:
    """YouTube / generic media: best progressive format (video+audio in one
    stream, since the overlay is a plain <video>), else an HLS manifest."""
    fmts = info.get("formats") or []
    for f in reversed(fmts):
        proto = str(f.get("protocol") or "")
        if (f.get("url") and proto.startswith("http") and "m3u8" not in proto
                and f.get("vcodec") not in (None, "none")
                and f.get("acodec") not in (None, "none")):
            return f["url"], "mp4"
    for f in reversed(fmts):
        if "m3u8" in str(f.get("protocol") or "") and f.get("url") \
                and f.get("vcodec") not in (None, "none"):
            return f["url"], "hls"
    url = info.get("url") or ""
    if url:
        return url, ("hls" if ".m3u8" in url else "mp4")
    return "", ""


# Resolved media URLs are short-lived; remember them briefly so play-right-
# after-add doesn't resolve twice.
_SRC_CACHE: dict[str, tuple[str, str, float]] = {}   # id -> (url, mode, when)
_SRC_TTL = 240.0
_RESOLVING: set[str] = set()


def _apply_info(entry: dict, info: dict) -> None:
    entry["title"] = info.get("title") or entry["title"] or entry["slug"]
    if info.get("duration"):
        entry["duration"] = round(float(info["duration"]), 2)
    entry["thumbnail"] = info.get("thumbnail") or entry["thumbnail"]
    entry["error"] = ""
    if entry["kind"] == "clip":
        url, mode = _pick_mp4(info), "mp4"
    elif entry["kind"] == "vod":
        url, mode = _pick_hls(info), "hls"
    else:
        url, mode = _pick_direct(info)
    if url:
        _SRC_CACHE[entry["id"]] = (url, mode, time.monotonic())


async def resolve_entry(eid: str) -> None:
    """Background metadata resolution after add (and pre-download for clips)."""
    entry = entry_by_id(eid)
    if entry is None or eid in _RESOLVING:
        return
    _RESOLVING.add(eid)
    try:
        try:
            info = await resolve_info(entry["url"])
        except Exception as exc:
            entry["error"] = str(exc)
            STATE.note(f"could not resolve #{entry['num']} {entry['url']}: {exc}")
            save_store()
            await HUB.broadcast_queue()
            return
        _apply_info(entry, info)
        save_store()
        await HUB.broadcast_queue()
        if entry["kind"] != "vod" and settings().get("predownload") \
                and not cache_path(entry).exists():
            await download_clip(entry)
    finally:
        _RESOLVING.discard(eid)


async def download_clip(entry: dict) -> None:
    """Pre-download a clip's MP4 to media/clips/{id}.mp4. Downloads go through
    the system temp dir and are moved into place in one rename - media/ is
    watched recursively by the soundboard, and a chunked download in there
    would trigger a re-index per write."""
    target = cache_path(entry)
    if target.exists():
        return
    tmp = Path(tempfile.gettempdir()) / f"hexcast_clip_{entry['id']}.mp4"
    if entry["kind"] == "clip":
        fmt = ["-f", "b[ext=mp4]/b"]
    else:
        # YouTube (and most sites) serve the good streams video-only + audio-
        # only; yt-dlp merges them with ffmpeg.
        fmt = ["-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
               "--merge-output-format", "mp4"]
    try:
        await _ytdlp(*fmt, "-o", str(tmp), entry["url"], timeout=300)
    except Exception as exc:
        STATE.note(f"pre-download failed for #{entry['num']} ({exc}) - will stream instead")
        tmp.unlink(missing_ok=True)
        return
    if tmp.exists():
        CLIPS_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), str(target))
        STATE.note(f"cached #{entry['num']} {entry['title'] or entry['slug']} "
                   f"({target.stat().st_size // 1024} KB)")
        await HUB.broadcast_queue()


# --------------------------------------------------------------------------
# player control - the server is the source of truth, the overlay executes
# --------------------------------------------------------------------------

_IFRAME_TIMER: asyncio.Task | None = None


def _cancel_iframe_timer() -> None:
    global _IFRAME_TIMER
    if _IFRAME_TIMER and not _IFRAME_TIMER.done():
        _IFRAME_TIMER.cancel()
    _IFRAME_TIMER = None


def iframe_source(entry: dict) -> dict | None:
    """Embed fallback. Twitch needs parent=<hostname>, which only the browser
    knows, so the overlay builds the final URL. Generic media has no embed."""
    if entry["kind"] == "media":
        return None
    return {"mode": "iframe", "kind": entry["kind"], "slug": entry["slug"],
            "start": entry.get("start") or 0.0}


async def build_source(entry: dict) -> dict:
    """Work out how the overlay should play this entry. Raises on failure."""
    start = entry.get("start") or 0.0
    target = cache_path(entry)
    if target.exists():
        return {"mode": "file",
                "url": f"/media/clips/{entry['id']}.mp4?v={int(target.stat().st_mtime)}",
                "start": start}

    cached = _SRC_CACHE.get(entry["id"])
    if cached and time.monotonic() - cached[2] < _SRC_TTL:
        url, mode = cached[0], cached[1]
    else:
        info = await resolve_info(entry["url"])
        _apply_info(entry, info)
        save_store()
        await HUB.broadcast_queue()
        c = _SRC_CACHE.get(entry["id"])
        url, mode = (c[0], c[1]) if c else ("", "")
    if not url:
        raise RuntimeError("no playable format found")
    return {"mode": mode, "url": url, "start": start}


async def play_entry(entry: dict) -> dict:
    _cancel_iframe_timer()
    if PLAYER["item_id"] != entry["id"]:
        EPHEMERAL.pop(PLAYER["item_id"], None)   # replaced mid-play
    start = entry.get("start") or 0.0
    PLAYER.update(state="loading", item_id=entry["id"], mode="",
                  position=start, duration=entry.get("duration") or 0.0)
    await HUB.broadcast_player()

    try:
        src = await build_source(entry)
    except Exception as exc:
        entry["error"] = str(exc)
        src = iframe_source(entry) if settings().get("iframe_fallback") else None
        if src is None:
            PLAYER.update(state="idle", item_id="", mode="", position=0.0, duration=0.0)
            save_store()
            await HUB.broadcast_queue()
            await HUB.broadcast_player()
            STATE.note(f"cannot play #{entry['num']}: {exc}")
            return {"ok": False, "error": str(exc)}
        STATE.note(f"direct playback failed for #{entry['num']} ({exc}) - trying the embed")
        save_store()
        await HUB.broadcast_queue()

    PLAYER.update(state="playing", mode=src["mode"])
    await HUB.to_overlay({"type": "play", "item": public_entry(entry),
                          "src": src, "volume": settings().get("volume", 1.0)})
    await HUB.broadcast_player()
    STATE.note(f"playing #{entry['num']} {entry['title'] or entry['slug']} ({src['mode']})")

    if src["mode"] == "iframe" and entry.get("duration"):
        # The iframe can't report ended; clear it ourselves when it should be done.
        _schedule_iframe_finish(entry, float(entry["duration"]) - start + 4.0)
    return {"ok": True, "player": player_snapshot()}


def _schedule_iframe_finish(entry: dict, delay: float) -> None:
    global _IFRAME_TIMER

    async def _wait() -> None:
        await asyncio.sleep(max(2.0, delay))
        if PLAYER["item_id"] == entry["id"] and PLAYER["mode"] == "iframe":
            await finish_current(mark_played=True)
            await HUB.to_overlay({"type": "stop"})

    _IFRAME_TIMER = asyncio.create_task(_wait())


async def finish_current(mark_played: bool) -> None:
    _cancel_iframe_timer()
    entry = entry_by_id(PLAYER["item_id"])
    if entry and mark_played and entry.get("status") != "played" \
            and PLAYER["item_id"] not in EPHEMERAL:
        entry["status"] = "played"
        save_store()
    EPHEMERAL.pop(PLAYER["item_id"], None)
    PLAYER.update(state="idle", item_id="", mode="", position=0.0, duration=0.0)
    await HUB.broadcast_queue()
    await HUB.broadcast_player()
    if SHOUTOUT_PENDING:
        nxt = SHOUTOUT_PENDING.pop(0)
        asyncio.create_task(play_entry(nxt))


def _drop_pending_shoutouts() -> None:
    for e in SHOUTOUT_PENDING:
        EPHEMERAL.pop(e["id"], None)
    SHOUTOUT_PENDING.clear()


async def stop_playback() -> dict:
    """Stop = clear the overlay, item stays queued and is NOT marked played."""
    playing = PLAYER["state"] in ("playing", "paused", "loading")
    _cancel_iframe_timer()
    _drop_pending_shoutouts()
    EPHEMERAL.pop(PLAYER["item_id"], None)
    PLAYER.update(state="idle", item_id="", mode="", position=0.0, duration=0.0)
    await HUB.to_overlay({"type": "stop"})
    await HUB.broadcast_player()
    return {"ok": True, "was_playing": playing}


async def set_paused(paused: bool) -> dict:
    if PLAYER["state"] not in ("playing", "paused"):
        return {"ok": False, "error": "nothing is playing"}
    if PLAYER["mode"] == "iframe":
        return {"ok": False, "error": "the Twitch embed fallback cannot be paused"}
    PLAYER["state"] = "paused" if paused else "playing"
    await HUB.to_overlay({"type": "pause" if paused else "resume"})
    await HUB.broadcast_player()
    return {"ok": True, "player": player_snapshot()}


async def _handle_overlay_msg(msg: dict) -> None:
    t = msg.get("type")
    eid = str(msg.get("id") or "")
    if t == "progress":
        if eid and eid != PLAYER["item_id"]:
            return
        try:
            PLAYER["position"] = float(msg.get("position") or 0.0)
            if msg.get("duration"):
                PLAYER["duration"] = float(msg["duration"])
        except (TypeError, ValueError):
            return
        await HUB.broadcast_player()
        return
    if t == "ended":
        if eid == PLAYER["item_id"] and PLAYER["state"] != "idle":
            await finish_current(mark_played=True)
        return
    if t == "error":
        if eid != PLAYER["item_id"] or PLAYER["state"] == "idle":
            return
        entry = entry_by_id(eid)
        detail = str(msg.get("message") or "playback error")
        if entry:
            entry["error"] = detail
            save_store()
        STATE.note(f"overlay playback error: {detail}")
        src = iframe_source(entry) if entry else None
        if src and PLAYER["mode"] != "iframe" and settings().get("iframe_fallback"):
            PLAYER.update(state="playing", mode="iframe")
            await HUB.to_overlay({"type": "play", "item": public_entry(entry),
                                  "src": src, "volume": settings().get("volume", 1.0)})
            if entry.get("duration"):
                _schedule_iframe_finish(
                    entry, float(entry["duration"]) - (entry.get("start") or 0.0) + 4.0)
            await HUB.broadcast_queue()
            await HUB.broadcast_player()
        else:
            await stop_playback()
            await HUB.broadcast_queue()


# --------------------------------------------------------------------------
# lazy startup
# --------------------------------------------------------------------------

async def ensure_started() -> None:
    """hexcast.py uses FastAPI(lifespan=...), which makes Starlette ignore
    add_event_handler("startup") - so the module starts lazily on the first
    request, like the other integrations."""
    if STATE.started:
        return
    STATE.started = True
    asyncio.create_task(_startup())


async def _startup() -> None:
    if not has_ytdlp():
        STATE.note("yt-dlp is not installed - queueing works, playback won't "
                   "(pip install -r requirements.txt)")
        return
    try:
        out = await _ytdlp("--version", timeout=30)
        STATE.ytdlp_version = out.decode("utf-8", errors="replace").strip()
        STATE.note(f"yt-dlp {STATE.ytdlp_version} ready")
    except Exception as exc:
        STATE.note(f"yt-dlp check failed: {exc}")
        return
    # Pick up anything added while we were down or that never resolved.
    for e in STORE["queue"]:
        needs_meta = not e.get("title") and not e.get("error")
        needs_dl = (e["kind"] != "vod" and settings().get("predownload")
                    and e.get("status") == "queued" and not e.get("error")
                    and not cache_path(e).exists())
        if needs_meta or needs_dl:
            asyncio.create_task(resolve_entry(e["id"]))


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/clips", tags=["clips"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    await ensure_started()
    return HTMLResponse(_read_static("clips_panel.html"))


@router.get("/overlay", response_class=HTMLResponse)
async def overlay():
    await ensure_started()
    return HTMLResponse(_read_static("clips_overlay.html"))


@router.get("/api/status")
async def api_status():
    return status_snapshot()


@router.get("/api/queue")
async def api_queue():
    return {"ok": True, "queue": [public_entry(e) for e in STORE["queue"]],
            "player": player_snapshot()}


@router.api_route("/api/add", methods=["GET", "POST"])
async def api_add(request: Request, url: str = "", text: str = ""):
    await ensure_started()
    blob = f"{url}\n{text}"
    source = "api"
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        blob += "\n" + str(body.get("url") or "") + "\n" + str(body.get("text") or "")
        if body.get("source") == "manual":
            source = "manual"

    links = extract_links(blob)
    if not links:
        return JSONResponse({"ok": False, "error": "no twitch clip or VOD links found"},
                            status_code=400)
    added, skipped = add_links(links, source)
    for e in added:
        asyncio.create_task(resolve_entry(e["id"]))
    await HUB.broadcast_queue()
    STATE.note(f"added {len(added)} item(s)" + (f", {skipped} duplicate(s) skipped" if skipped else ""))
    return {"ok": True, "added": [public_entry(e) for e in added], "skipped": skipped,
            "entry": public_entry(added[0]) if added else None}


@router.api_route("/api/play/{ref}", methods=["GET", "POST"])
async def api_play(ref: str):
    await ensure_started()
    entry = entry_by_ref(ref)
    if entry is None:
        return JSONResponse({"ok": False, "error": f"no queue item matches '{ref}'"},
                            status_code=404)
    _drop_pending_shoutouts()   # a manual play outranks a shoutout chain
    return await play_entry(entry)


@router.api_route("/api/pause", methods=["GET", "POST"])
async def api_pause():
    return await set_paused(True)


@router.api_route("/api/resume", methods=["GET", "POST"])
async def api_resume():
    return await set_paused(False)


@router.api_route("/api/toggle", methods=["GET", "POST"])
async def api_toggle():
    return await set_paused(PLAYER["state"] == "playing")


@router.api_route("/api/stop", methods=["GET", "POST"])
async def api_stop():
    return await stop_playback()


async def _remove_ref(ref: str) -> dict | JSONResponse:
    entry = entry_by_ref(ref)
    if entry is None:
        return JSONResponse({"ok": False, "error": f"no queue item matches '{ref}'"},
                            status_code=404)
    if entry["id"] == PLAYER["item_id"]:
        await stop_playback()
    STORE["queue"] = [e for e in STORE["queue"] if e["id"] != entry["id"]]
    save_store()
    cache_path(entry).unlink(missing_ok=True)
    _SRC_CACHE.pop(entry["id"], None)
    await HUB.broadcast_queue()
    return {"ok": True, "removed": public_entry(entry)}


@router.delete("/api/queue/{ref}")
async def api_delete(ref: str):
    return await _remove_ref(ref)


@router.api_route("/api/remove/{ref}", methods=["GET", "POST"])
async def api_remove(ref: str):
    # GET alias for bots that can't send DELETE.
    return await _remove_ref(ref)


@router.post("/api/reorder")
async def api_reorder(request: Request):
    body = await request.json()
    order = [str(x) for x in (body.get("order") or [])]
    if not order:
        return JSONResponse({"ok": False, "error": "order (list of ids) required"},
                            status_code=400)
    pos = {eid: i for i, eid in enumerate(order)}
    STORE["queue"].sort(key=lambda e: pos.get(e["id"], len(pos) + e["num"]))
    save_store()
    await HUB.broadcast_queue()
    return {"ok": True}


@router.post("/api/mark")
async def api_mark(request: Request):
    body = await request.json()
    entry = entry_by_ref(str(body.get("ref") or ""))
    if entry is None:
        return JSONResponse({"ok": False, "error": "no such item"}, status_code=404)
    entry["status"] = "played" if body.get("played") else "queued"
    save_store()
    await HUB.broadcast_queue()
    return {"ok": True, "entry": public_entry(entry)}


@router.post("/api/reset_numbers")
async def api_reset_numbers():
    """Renumber the queue 1..N in current order and restart the counter.
    Numbers are deliberately never reused otherwise (bots reference them), so
    this is a manual action for when the count has crept up."""
    for i, e in enumerate(STORE["queue"], start=1):
        e["num"] = i
    STORE["seq"] = len(STORE["queue"])
    save_store()
    await HUB.broadcast_queue()
    await HUB.broadcast_player()   # the current item's number may have changed
    return {"ok": True, "seq": STORE["seq"]}


@router.post("/api/clear_played")
async def api_clear_played():
    gone = [e for e in STORE["queue"] if e.get("status") == "played"
            and e["id"] != PLAYER["item_id"]]
    STORE["queue"] = [e for e in STORE["queue"] if e not in gone]
    save_store()
    for e in gone:
        cache_path(e).unlink(missing_ok=True)
        _SRC_CACHE.pop(e["id"], None)
    await HUB.broadcast_queue()
    return {"ok": True, "removed": len(gone)}


TWITCH_LOGIN = re.compile(r"^[A-Za-z0-9_]{2,25}$")


async def _random_channel_clips(channel: str, count: int = 1) -> list[str]:
    """Random distinct clip URLs from a channel's clips page: top clips of the
    last 30 days first, all-time as the fallback for quieter channels."""
    for rng in ("30d", "all"):
        try:
            out = await _ytdlp(
                "--flat-playlist", "-I", "1:30", "-j",
                f"https://www.twitch.tv/{channel}/clips?filter=clips&range={rng}",
                timeout=60)
        except Exception:
            continue
        urls, seen = [], set()
        for line in out.decode("utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            u = d.get("url") or d.get("webpage_url") or ""
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        if urls:
            return random.sample(urls, min(count, len(urls)))
    return []


async def play_shoutout(channel: str, count: int = 2) -> dict:
    """Play random clips from someone's channel through the overlay without
    queueing them - they chain back to back as each ends. The Twitch module's
    !so command calls this directly; /clips/api/shoutout exposes it to bots."""
    await ensure_started()
    channel = channel.strip().lstrip("@").lower()
    if not TWITCH_LOGIN.match(channel):
        return {"ok": False, "error": "bad channel name"}
    count = max(1, min(int(count or 1), 5))

    urls = await _random_channel_clips(channel, count)
    if not urls:
        return {"ok": False, "error": f"no clips found for {channel}"}

    _drop_pending_shoutouts()
    entries = []
    for url in urls:
        links = extract_links(url)
        link = links[0] if links else {"kind": "media", "slug": channel, "start": 0.0, "url": url}
        entry = {
            "id": f"so{secrets.token_hex(3)}",
            "num": "SO",
            "url": link["url"],
            "kind": link["kind"],
            "slug": link["slug"],
            "start": link["start"],
            "title": "",
            "duration": None,
            "thumbnail": "",
            "status": "queued",
            "error": "",
            "added_ts": time.time(),
            "source": "shoutout",
        }
        EPHEMERAL[entry["id"]] = entry
        entries.append(entry)
    STATE.note(f"shoutout: {len(entries)} clip(s) for {channel}")

    result: dict = {"ok": False, "error": "no playable clips"}
    for i, entry in enumerate(entries):
        result = await play_entry(entry)
        if result.get("ok"):
            SHOUTOUT_PENDING.extend(entries[i + 1:])
            break
        EPHEMERAL.pop(entry["id"], None)
    result["channel"] = channel
    result["clips"] = [e["url"] for e in entries]
    return result


@router.api_route("/api/shoutout/{channel}", methods=["GET", "POST"])
async def api_shoutout(channel: str, count: int = 2):
    result = await play_shoutout(channel, count)
    if not result.get("ok"):
        code = 400 if result.get("error") == "bad channel name" else 404
        return JSONResponse(result, status_code=code)
    return result


@router.get("/api/config")
async def api_get_config():
    return settings()


@router.post("/api/config")
async def api_set_config(request: Request):
    incoming = await request.json()
    STORE["settings"] = _deep_merge(settings(), incoming)
    save_store()
    await HUB.broadcast_config()
    return {"ok": True, "config": settings()}


@router.websocket("/ws/overlay")
async def ws_overlay(ws: WebSocket):
    await ws.accept()
    HUB.overlay.add(ws)
    await ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "config", "config": settings()}))
        # A reloaded OBS source rejoins mid-clip instead of sitting blank.
        if PLAYER["state"] in ("playing", "paused") and PLAYER["item_id"]:
            asyncio.create_task(_resume_overlay(ws))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            await _handle_overlay_msg(msg)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.overlay.discard(ws)


async def _resume_overlay(ws: WebSocket) -> None:
    entry = entry_by_id(PLAYER["item_id"])
    if not entry:
        return
    try:
        src = iframe_source(entry) if PLAYER["mode"] == "iframe" else await build_source(entry)
        if src is None:
            return
        src = dict(src)
        src["start"] = PLAYER["position"] or src.get("start") or 0.0
        await ws.send_text(json.dumps({
            "type": "play", "item": public_entry(entry), "src": src,
            "volume": settings().get("volume", 1.0),
            "paused": PLAYER["state"] == "paused",
        }))
    except Exception as exc:
        STATE.note(f"could not resume playback on a reconnected overlay: {exc}")


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    HUB.panel.add(ws)
    await ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "config", "config": settings()}))
        await ws.send_text(json.dumps({"type": "queue",
                                       "queue": [public_entry(e) for e in STORE["queue"]]}))
        await ws.send_text(json.dumps({"type": "player", "player": player_snapshot()}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.panel.discard(ws)


# --------------------------------------------------------------------------
# attach
# --------------------------------------------------------------------------

async def start_clips() -> None:
    """Call from inside hexcast's lifespan startup."""
    await ensure_started()


async def stop_clips() -> None:
    """Call from inside hexcast's lifespan shutdown."""
    _cancel_iframe_timer()


def attach_clips(app, port: int = 4747) -> None:
    """Mount the Clips routes onto an existing FastAPI app.

    hexcast.py builds its app with FastAPI(lifespan=...), so Starlette ignores
    add_event_handler("startup"). The module therefore starts lazily on the
    first panel or overlay request. Call start_clips()/stop_clips() from that
    lifespan to start at boot instead.
    """
    app.include_router(router)
    print(f"  Clips panel:         http://localhost:{port}/clips", flush=True)
    print(f"  Clips overlay:       http://localhost:{port}/clips/overlay", flush=True)
