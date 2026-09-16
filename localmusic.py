"""
HexCast - Local music player module
====================================

A companion to the YouTube Music module. Where `ytmusic.py` only *observes* the
YouTube Music Desktop app, this adds a real local-file player: map a music
directory, browse/search it, build a queue, and play it through the SAME
now-playing overlay - the audio plays inside the OBS browser source itself, so
OBS captures it directly and the visualiser can react to it via the Web Audio
API (no desktop loopback needed).

It shares the overlay, panel, websocket hub, config and now-playing contract
with `ytmusic.py`. A `source` config key ("ytm" | "local") decides which player
drives the overlay. This module deliberately depends on ytmusic (one-way):
`ytmusic.attach_ytm()` late-imports this module at mount time, so there is no
import cycle and a single source of truth for the shared overlay is preserved.

Everything mounts under the same `/ytm` prefix:

    GET  /ytm/library/status              -> index progress
    GET  /ytm/library/browse?path=<rel>   -> one directory level
    GET  /ytm/library/search?q=<text>     -> substring search across the tree
    GET  /ytm/library/track/{id}          -> lazy metadata for one track
    GET  /ytm/queue                       -> the queue + player state
    POST /ytm/queue/add|remove|move|clear|play
    GET  /ytm/localart/{id}               -> embedded cover art (cached jpg)
    GET  /ytm/localfile/{id}              -> the audio file (HTTP Range for seeking)

Track metadata (title/artist/album/duration + embedded cover art) is read
lazily with ffprobe/ffmpeg - the same tools Hexcast already relies on - and
cached, so a 14k-file library never gets probed up front.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import string
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

import ytmusic  # sibling module; safe (attach_ytm late-imports us, so ytmusic is fully loaded)

# --------------------------------------------------------------------------
# paths / constants
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("HEXCAST_CONFIG_DIR", BASE_DIR / "config"))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_PATH = CONFIG_DIR / "localmusic.json"       # queue + player state (mutated often)
PLAYLISTS_PATH = CONFIG_DIR / "playlists.json"    # saved playlists: name -> {ids, created}
META_PATH = CONFIG_DIR / "localmusic_meta.json"   # probed tag/duration cache (warm restart)
INDEX_PATH = CONFIG_DIR / "localmusic_index.json" # cached library index (rebuilt only on Re-scan)
ART_CACHE_DIR = CONFIG_DIR / "localart"
ART_CACHE_DIR.mkdir(parents=True, exist_ok=True)

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma"}

# CEF is picky about content types (especially FLAC/OGG), so be explicit rather
# than trusting mimetypes.guess_type.
CTYPES = {
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".opus": "audio/ogg", ".wav": "audio/wav", ".wma": "audio/x-ms-wma",
}

_LOOP: asyncio.AbstractEventLoop | None = None   # captured on attach for thread->async hops


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


HAS_FFMPEG = _has_ffmpeg()


def _note(msg: str) -> None:
    print(f"[localmusic] {msg}", flush=True)


def _track_id(abspath: str) -> str:
    """Stable opaque id for a file. Path-derived so a re-index reproduces the same
    ids (saved queues survive restarts) and it doubles as the traversal boundary:
    only ids we've indexed map back to a path."""
    norm = os.path.normcase(os.path.abspath(abspath))
    return hashlib.sha1(norm.encode("utf-8", "surrogatepass")).hexdigest()[:16]


# --------------------------------------------------------------------------
# metadata (lazy, cached) - ffprobe for tags/duration, ffmpeg for cover art
# --------------------------------------------------------------------------

_meta_cache: dict[str, dict] = {}   # (path|mtime) key -> {title,artist,album,duration}
_meta_by_id: dict[str, dict] = {}   # tid -> meta, so the shallow path never stats the disk
_meta_lock = threading.Lock()


def _load_meta_cache() -> None:
    if META_PATH.exists():
        try:
            data = json.loads(META_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _meta_cache.update(data)
        except Exception:
            pass


def _save_meta_cache() -> None:
    try:
        META_PATH.write_text(json.dumps(_meta_cache), encoding="utf-8")
    except Exception:
        pass


def _meta_key(path: Path) -> str:
    try:
        return f"{path}|{path.stat().st_mtime_ns}"
    except OSError:
        return str(path)


def _ci_get(tags: dict, *names: str) -> str:
    low = {str(k).lower(): v for k, v in (tags or {}).items()}
    for n in names:
        v = low.get(n.lower())
        if v:
            return str(v).strip()
    return ""


def probe_meta(path: Path) -> dict:
    """Title/artist/album/duration for one file via a single ffprobe call. Cached
    by (path, mtime). Returns {} on failure or when ffmpeg is unavailable."""
    key = _meta_key(path)
    with _meta_lock:
        hit = _meta_cache.get(key)
    if hit is not None:
        return hit
    meta: dict[str, Any] = {}
    if HAS_FFMPEG:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", str(path)],
                timeout=8, capture_output=True, text=True,
            )
            if r.returncode == 0 and r.stdout:
                doc = json.loads(r.stdout)
                fmt = doc.get("format") or {}
                tags = dict(fmt.get("tags") or {})
                # FLAC/OGG Vorbis comments often live on the audio stream, not format.
                for st in doc.get("streams") or []:
                    if st.get("codec_type") == "audio":
                        for k, v in (st.get("tags") or {}).items():
                            tags.setdefault(k, v)
                dur = fmt.get("duration")
                meta = {
                    "title": _ci_get(tags, "title"),
                    "artist": _ci_get(tags, "artist", "album_artist", "albumartist"),
                    "album": _ci_get(tags, "album"),
                    "duration": round(float(dur), 3) if dur not in (None, "", "N/A") else 0.0,
                }
        except (subprocess.SubprocessError, OSError, ValueError, json.JSONDecodeError):
            meta = {}
    with _meta_lock:
        _meta_cache[key] = meta
        _save_meta_cache()
    return meta


def ensure_art(path: Path, tid: str) -> Path | None:
    """Extract embedded cover art to a cached jpg. Returns the jpg path, or None
    if there's none / ffmpeg is missing. Reused if fresh (by source mtime)."""
    if not HAS_FFMPEG:
        return None
    art = ART_CACHE_DIR / f"{tid}.jpg"
    miss = ART_CACHE_DIR / f"{tid}.none"   # marker: probed, no embedded art
    try:
        src_mtime = path.stat().st_mtime
    except OSError:
        return None
    if art.exists() and art.stat().st_mtime >= src_mtime:
        return art
    if miss.exists() and miss.stat().st_mtime >= src_mtime:
        return None
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
             "-map", "0:v", "-frames:v", "1", "-q:v", "5", str(art)],
            timeout=15, capture_output=True,
        )
        if r.returncode == 0 and art.exists():
            return art
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        miss.write_text("", encoding="utf-8")   # remember the miss so we don't retry every time
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------
# library index
# --------------------------------------------------------------------------

class LibraryIndex:
    """Flat, filename-level index of the music directory, built once in a
    background thread. Metadata stays lazy - only browse/search names are held."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.root: Path | None = None
        self.by_id: dict[str, Path] = {}
        self.entries: list[dict] = []       # {id, name, folder(posix)} for search
        self.tree: dict[str, dict] = {}     # folder(posix) -> {dirs:[rel...], tracks:[{id,name}]}
        self.indexing = False
        self.ready = False
        self.thread: threading.Thread | None = None
        self.error = ""

    def status(self) -> dict:
        with self.lock:
            return {
                "root": str(self.root) if self.root else "",
                "indexing": self.indexing,
                "ready": self.ready,
                "count": len(self.by_id),
                "error": self.error,
                "ffmpeg": HAS_FFMPEG,
            }

    def set_root(self, path_str: str) -> None:
        path_str = (path_str or "").strip()
        new_root = Path(path_str).resolve() if path_str else None
        with self.lock:
            same = (self.root == new_root)
        if same and (self.ready or self.indexing):
            return
        self._start(new_root)

    def _start(self, new_root: Path | None, force: bool = False) -> None:
        """Point at a folder. Loads the cached index from disk when possible (no
        walk); only walks the filesystem on first use, a new folder, or when
        `force` (the Re-scan button) is set."""
        with self.lock:
            self.root = new_root
            self.by_id = {}
            self.entries = []
            self.tree = {}
            self.ready = False
            self.error = ""
            self.indexing = bool(new_root)
        if not new_root:
            return
        if not new_root.exists() or not new_root.is_dir():
            with self.lock:
                self.indexing = False
                self.error = f"not a directory: {new_root}"
            _note(self.error)
            return
        if not force and self._load_from_disk(new_root):
            with self.lock:
                self.indexing = False
                self.ready = True
            _note(f"index loaded from cache: {len(self.by_id)} tracks (Re-scan to refresh)")
            return
        self.thread = threading.Thread(target=self._build, args=(new_root,),
                                       name="localmusic-index", daemon=True)
        self.thread.start()

    @staticmethod
    def _build_tree(entries: list[dict]) -> dict:
        """Folder tree for instant browsing, built once from the flat index."""
        tree: dict[str, dict] = {"": {"dirs": set(), "tracks": []}}
        for e in entries:
            folder = e["folder"]
            tree.setdefault(folder, {"dirs": set(), "tracks": []})
            tree[folder]["tracks"].append({"id": e["id"], "name": e["name"]})
            if folder:
                parts = folder.split("/")
                for i in range(len(parts)):
                    parent = "/".join(parts[:i])
                    child = "/".join(parts[:i + 1])
                    tree.setdefault(parent, {"dirs": set(), "tracks": []})["dirs"].add(child)
                    tree.setdefault(child, {"dirs": set(), "tracks": []})
        return {k: {"dirs": sorted(v["dirs"], key=str.lower), "tracks": v["tracks"]}
                for k, v in tree.items()}

    def _apply(self, root: Path, by_id: dict, entries: list[dict]) -> None:
        with self.lock:
            if self.root != root:
                return
            self.by_id = by_id
            self.entries = entries
            self.tree = self._build_tree(entries)
            self.indexing = False
            self.ready = True

    def _build(self, root: Path) -> None:
        _note(f"indexing {root} ...")
        by_id: dict[str, Path] = {}
        entries: list[dict] = []
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames.sort()
                for fn in sorted(filenames):
                    if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                        p = Path(dirpath) / fn
                        tid = _track_id(str(p))
                        by_id[tid] = p
                        rel_dir = p.parent.relative_to(root)
                        folder = "" if str(rel_dir) == "." else rel_dir.as_posix()
                        entries.append({"id": tid, "name": p.stem, "folder": folder})
        except Exception as exc:            # pragma: no cover - fs surprises
            with self.lock:
                self.indexing = False
                self.error = str(exc)
            _note(f"index error: {exc}")
            return
        with self.lock:
            if self.root != root:
                _note("index superseded, aborting")
                return
        self._apply(root, by_id, entries)
        self._save_to_disk(root)
        _note(f"index ready: {len(by_id)} tracks (cached to disk)")

    def _save_to_disk(self, root: Path) -> None:
        with self.lock:
            if self.root != root:
                return
            data = {"root": str(root),
                    "entries": [{"id": e["id"], "path": str(self.by_id.get(e["id"], "")),
                                 "name": e["name"], "folder": e["folder"]}
                                for e in self.entries]}
        try:
            INDEX_PATH.write_text(json.dumps(data), encoding="utf-8")
        except Exception as exc:
            _note(f"index save failed: {exc}")

    def _load_from_disk(self, root: Path) -> bool:
        if not INDEX_PATH.exists():
            return False
        try:
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            return False
        if str(root) != data.get("root"):
            return False
        by_id: dict[str, Path] = {}
        entries: list[dict] = []
        for e in data.get("entries") or []:
            tid, p = e.get("id"), e.get("path")
            if not tid or not p:
                continue
            by_id[tid] = Path(p)
            entries.append({"id": tid, "name": e.get("name", ""), "folder": e.get("folder", "")})
        self._apply(root, by_id, entries)
        return True

    def path_for(self, tid: str) -> Path | None:
        with self.lock:
            return self.by_id.get(tid)

    def browse(self, rel: str) -> dict:
        """Serve one folder level straight from the in-memory tree - no filesystem
        access, so it's instant even on a 15k-file library."""
        with self.lock:
            root = self.root
            tree = self.tree
            indexing = self.indexing
        if root is None:
            return {"error": "no music directory set", "root": "", "rel": "",
                    "parent": None, "dirs": [], "tracks": [], "indexing": False}
        rel = (rel or "").strip().replace("\\", "/").strip("/")
        node = tree.get(rel)
        if node is None:
            rel, node = "", tree.get("", {"dirs": [], "tracks": []})
        dirs = [{"name": c.split("/")[-1], "rel": c} for c in node["dirs"]]
        parent = None if rel == "" else "/".join(rel.split("/")[:-1])
        return {"root": str(root), "rel": rel, "parent": parent,
                "dirs": dirs, "tracks": node["tracks"], "indexing": indexing}

    def search(self, q: str, limit: int = 500) -> dict:
        q = (q or "").strip().lower()
        with self.lock:
            entries = self.entries
            indexing = self.indexing
        if not q:
            return {"results": [], "truncated": False, "indexing": indexing}
        results = []
        for e in entries:
            if q in e["name"].lower() or q in e["folder"].lower():
                results.append(e)
                if len(results) >= limit:
                    return {"results": results, "truncated": True, "indexing": indexing}
        return {"results": results, "truncated": False, "indexing": indexing}

    def ids_in_folder(self, rel: str, recursive: bool = True) -> list[str]:
        """Track ids under a folder (relative to root), in index order. Empty rel
        + recursive = the whole library. Done server-side so the browser never has
        to enumerate or POST tens of thousands of ids."""
        rel = (rel or "").strip().replace("\\", "/").strip("/")
        with self.lock:
            entries = self.entries
        out = []
        for e in entries:
            folder = e["folder"]
            if not rel:
                if recursive or folder == "":
                    out.append(e["id"])
            elif recursive:
                if folder == rel or folder.startswith(rel + "/"):
                    out.append(e["id"])
            elif folder == rel:
                out.append(e["id"])
        return out


INDEX = LibraryIndex()


def track_view(tid: str, deep: bool = False) -> dict:
    """A display record for one track. Cheap by default (index lookup only, no
    filesystem stat) so building a view of a huge queue stays fast. `deep` shells
    out to ffprobe for tags/duration (cached, and only for the now-playing track).
    "missing" is decided from the index alone; a file deleted since the last index
    still resolves here and simply errors on play, which skips it."""
    p = INDEX.path_for(tid)
    if p is None:
        return {"id": tid, "name": "(missing)", "missing": True,
                "title": "", "artist": "", "album": "", "duration": 0.0, "art": ""}
    view = {"id": tid, "name": p.stem, "missing": False,
            "title": "", "artist": "", "album": "", "duration": 0.0,
            "art": f"/ytm/localart/{tid}"}
    meta = _meta_by_id.get(tid)          # in-memory, no disk touch
    if meta is None and deep:
        meta = probe_meta(p)
        _meta_by_id[tid] = meta
    if meta:
        view.update({"title": meta.get("title", ""), "artist": meta.get("artist", ""),
                     "album": meta.get("album", ""), "duration": meta.get("duration", 0.0)})
    return view


# --------------------------------------------------------------------------
# player + queue
# --------------------------------------------------------------------------

class LocalPlayer:
    """Owns the queue and the playback *intent*. The overlay is the actual audio
    element: it obeys control events (identified by a bumped `seq`) and reports
    position / ended back, which advances the queue here."""

    def __init__(self) -> None:
        self.queue: list[str] = []
        self.index: int = 0
        self.playing: bool = False
        self.position: float = 0.0
        self.duration: float = 0.0
        self.repeat: str = "off"        # off | all | one
        self.shuffle: bool = False
        self.volume: int = 100
        self.seq: int = 0               # bumps on every control event
        self._pos_pushed: float = 0.0
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if not QUEUE_PATH.exists():
            return
        try:
            d = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        self.queue = [str(x) for x in (d.get("queue") or [])]
        self.index = int(d.get("index") or 0)
        self.repeat = d.get("repeat") if d.get("repeat") in ("off", "all", "one") else "off"
        self.shuffle = bool(d.get("shuffle"))
        self.volume = max(0, min(100, int(d.get("volume") or 100)))
        if self.queue:
            self.index = max(0, min(self.index, len(self.queue) - 1))
        else:
            self.index = 0

    def _save(self) -> None:
        try:
            QUEUE_PATH.write_text(json.dumps({
                "queue": self.queue, "index": self.index, "repeat": self.repeat,
                "shuffle": self.shuffle, "volume": self.volume,
            }, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _bump(self) -> None:
        self.seq += 1

    def current_id(self) -> str:
        if 0 <= self.index < len(self.queue):
            return self.queue[self.index]
        return ""

    # ---- queue edits ----
    def add(self, ids: list[str], at: int | None = None) -> None:
        ids = [str(i) for i in ids if i]
        if not ids:
            return
        was_empty = not self.queue
        if at is None or at < 0 or at > len(self.queue):
            self.queue.extend(ids)
        else:
            self.queue[at:at] = ids
            if at <= self.index:
                self.index += len(ids)
        if was_empty:
            self.index = 0
            self._bump()      # first tracks arrived -> overlay should load one
        self._save()

    def remove(self, indices: list[int]) -> None:
        drop = sorted({i for i in indices if 0 <= i < len(self.queue)}, reverse=True)
        if not drop:
            return
        cur = self.current_id()
        for i in drop:
            del self.queue[i]
        # keep pointing at the same track if it survived, else clamp
        if cur and cur in self.queue and self.queue[self.index:self.index + 1] != [cur]:
            self.index = self.queue.index(cur)
        else:
            self.index = max(0, min(self.index, len(self.queue) - 1)) if self.queue else 0
            if not self.queue:
                self.playing = False
                self._bump()
        self._save()

    def move(self, frm: int, to: int) -> None:
        n = len(self.queue)
        if not (0 <= frm < n) or not (0 <= to < n) or frm == to:
            return
        cur = self.current_id()
        item = self.queue.pop(frm)
        self.queue.insert(to, item)
        if cur:
            self.index = self.queue.index(cur)
        self._save()

    def clear(self) -> None:
        self.queue = []
        self.index = 0
        self.playing = False
        self.position = 0.0
        self._bump()
        self._save()

    # ---- transport ----
    def play(self) -> None:
        if self.queue:
            self.playing = True
            self._bump()
            self._save()

    def pause(self) -> None:
        self.playing = False
        self._bump()

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def play_index(self, idx: int) -> None:
        if 0 <= idx < len(self.queue):
            self.index = idx
            self.position = 0.0
            self.playing = True
            self._bump()
            self._save()

    def seek(self, seconds: float) -> None:
        self.position = max(0.0, float(seconds))
        self._bump()

    def set_volume(self, vol: int) -> None:
        self.volume = max(0, min(100, int(vol)))
        self._bump()
        self._save()

    def set_repeat(self, mode: str) -> None:
        if mode in ("off", "all", "one"):
            self.repeat = mode
            self._save()

    def set_shuffle(self, on: bool) -> None:
        self.shuffle = bool(on)
        self._save()

    def _step(self, delta: int, user: bool) -> None:
        n = len(self.queue)
        if n == 0:
            self.playing = False
            self._bump()
            return
        if self.shuffle and delta > 0:
            self.index = random.randrange(n) if n > 1 else 0
        else:
            ni = self.index + delta
            if ni >= n:
                if self.repeat == "all":
                    ni = 0
                else:
                    self.index = n - 1
                    self.position = 0.0
                    self.playing = False
                    self._bump()
                    return
            elif ni < 0:
                ni = 0
            self.index = ni
        self.position = 0.0
        self.playing = True
        self._bump()

    def next(self, user: bool = True) -> None:
        self._step(+1, user)

    def prev(self) -> None:
        # Common player behaviour: restart the track if we're past the intro.
        if self.position > 3.0:
            self.position = 0.0
            self._bump()
        else:
            self._step(-1, True)

    # ---- overlay reports ----
    def on_track_ended(self) -> None:
        if self.repeat == "one":
            self.position = 0.0
            self._bump()
        else:
            self._step(+1, user=False)

    def on_progress(self, pos: float, duration: float) -> bool:
        """Store position from the overlay. Returns True if the panel should be
        refreshed (throttled to ~1/s so reports don't flood)."""
        self.position = max(0.0, float(pos or 0))
        if duration:
            self.duration = float(duration)
        now = time.time()
        if now - self._pos_pushed >= 1.0:
            self._pos_pushed = now
            return True
        return False

    # ---- payloads ----
    def build_state(self) -> dict:
        tid = self.current_id()
        base: dict[str, Any] = {
            "type": "state", "ts": time.time(), "source": "local",
            "seq": self.seq, "playing": self.playing,
            "state": "playing" if self.playing else "paused",
            "ad": False, "is_live": False, "video_type": None,
            "counterpart_id": "", "like": None,
            "volume": self.volume, "repeat": self.repeat, "shuffle": self.shuffle,
            "index": self.index, "queue_len": len(self.queue),
            "progress": self.position,
        }
        if tid:
            v = track_view(tid, deep=True)
            dur = v["duration"] or self.duration or 0.0
            base.update({
                "id": tid, "src": f"/ytm/localfile/{tid}",
                "title": v["title"] or v["name"], "author": v["artist"],
                "album": v["album"], "duration": dur,
                "art": "" if v["missing"] else f"/ytm/localart/{tid}",
                "missing": v["missing"],
            })
        else:
            base.update({"id": "", "src": "", "title": "", "author": "", "album": "",
                         "duration": 0.0, "art": "", "missing": False})
        # up-next preview
        nxt_i = self.index + 1
        if 0 <= nxt_i < len(self.queue):
            nv = track_view(self.queue[nxt_i])
            base["next"] = {"title": nv["title"] or nv["name"], "author": nv["artist"]}
        else:
            base["next"] = None
        return base

    def state_view(self) -> dict:
        """Lightweight queue/player state - no item list, so it's cheap to send
        even with a 15k-track queue. The panel fetches item windows separately."""
        cur = track_view(self.current_id(), deep=True) if self.current_id() else None
        return {
            "count": len(self.queue), "index": self.index, "playing": self.playing,
            "position": self.position, "duration": self.duration,
            "repeat": self.repeat, "shuffle": self.shuffle, "volume": self.volume,
            "source": ytmusic.active_source(), "current": cur,
        }

    def items_view(self, offset: int, limit: int) -> dict:
        """A window of the queue for the panel's virtualised list."""
        n = len(self.queue)
        offset = max(0, int(offset))
        limit = max(1, min(1000, int(limit)))
        window = self.queue[offset:offset + limit]
        return {"offset": offset, "limit": limit, "count": n, "index": self.index,
                "items": [track_view(t) for t in window]}


PLAYER = LocalPlayer()


# --------------------------------------------------------------------------
# playlists (named saved queues, stored as JSON)
# --------------------------------------------------------------------------

def _load_playlists() -> dict:
    if PLAYLISTS_PATH.exists():
        try:
            d = json.loads(PLAYLISTS_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


PLAYLISTS: dict[str, dict] = _load_playlists()   # name -> {"ids": [...], "created": ts}


def _save_playlists() -> None:
    try:
        PLAYLISTS_PATH.write_text(json.dumps(PLAYLISTS, indent=2), encoding="utf-8")
    except Exception:
        pass


def playlists_list() -> list[dict]:
    return [{"name": n, "count": len(v.get("ids") or [])}
            for n, v in sorted(PLAYLISTS.items(), key=lambda kv: kv[0].lower())]


# --------------------------------------------------------------------------
# broadcasting (into the shared ytmusic hub)
# --------------------------------------------------------------------------

async def broadcast_now() -> None:
    """Push the now-playing state (current track only) to overlay + panel. Only
    sets the shared STATE.now when local is the active source, so /api/nowplaying
    and late-joiner seeding reflect whatever is actually driving the overlay.
    This is the ONLY thing sent on a track change - never the whole queue."""
    global _LOOP
    try:
        _LOOP = asyncio.get_running_loop()
    except RuntimeError:
        pass
    payload = PLAYER.build_state()
    if ytmusic.active_source() == "local":
        ytmusic.STATE.now = payload
        await ytmusic.HUB.to_overlay(payload)
    await ytmusic.HUB.to_panel({"type": "now", "now": payload})


async def notify_queue() -> None:
    """Tell the panel the queue *structure* changed (add/remove/move/clear/load).
    A tiny signal - the panel refetches only the window it's showing."""
    await ytmusic.HUB.to_panel({"type": "queue_changed",
                                "count": len(PLAYER.queue), "index": PLAYER.index})


async def notify_index() -> None:
    """Tell the panel the current index moved (track change) without resending items."""
    await ytmusic.HUB.to_panel({"type": "queue_index",
                                "index": PLAYER.index, "count": len(PLAYER.queue)})


async def _after_transport() -> None:
    await broadcast_now()
    await notify_index()


async def _after_queue_edit() -> None:
    await broadcast_now()
    await notify_queue()


# --------------------------------------------------------------------------
# command routing (called by ytmusic's source-aware /api/command)
# --------------------------------------------------------------------------

async def handle_command(cmd: str, data: Any = None) -> tuple[bool, str]:
    cmd = (cmd or "").strip()
    if cmd in ("playPause", "toggle"):
        PLAYER.toggle()
    elif cmd == "play":
        PLAYER.play()
    elif cmd == "pause":
        PLAYER.pause()
    elif cmd == "next":
        PLAYER.next()
    elif cmd == "previous":
        PLAYER.prev()
    elif cmd == "seekTo":
        PLAYER.seek(float((data or {}).get("seconds", data) if isinstance(data, dict) else (data or 0)))
    elif cmd == "setVolume":
        PLAYER.set_volume(int((data or {}).get("volume", data) if isinstance(data, dict) else (data or 0)))
    elif cmd == "playQueueIndex" or cmd == "playIndex":
        PLAYER.play_index(int((data or {}).get("index", data) if isinstance(data, dict) else (data or 0)))
    elif cmd == "repeatMode":
        PLAYER.set_repeat(str((data or {}).get("mode", data) if isinstance(data, dict) else data))
    elif cmd == "shuffle":
        PLAYER.set_shuffle(not PLAYER.shuffle if data is None else bool(data))
    else:
        return False, f"unknown local command: {cmd}"
    await _after_transport()
    return True, ""


async def handle_overlay_message(msg: dict) -> None:
    """Inbound messages from the overlay's <audio> element (player role)."""
    t = msg.get("type")
    if t == "local_progress":
        if PLAYER.on_progress(msg.get("pos", 0), msg.get("duration", 0)):
            await ytmusic.HUB.to_panel({"type": "now", "now": PLAYER.build_state()})
    elif t == "local_ended":
        PLAYER.on_track_ended()
        await _after_transport()
    elif t == "local_error":
        _note(f"overlay reported playback error for {msg.get('id')}; skipping")
        PLAYER.on_track_ended()
        await _after_transport()
    elif t == "local_ready":
        d = msg.get("duration")
        if d:
            PLAYER.duration = float(d)


async def activate() -> None:
    """Called by ytmusic when the active source becomes 'local'."""
    await broadcast_now()
    await notify_queue()


def reindex_from_config() -> None:
    """Point the index at the configured directory (called on attach + config change)."""
    music_dir = (ytmusic.CONFIG.get("local") or {}).get("music_dir", "")
    INDEX.set_root(music_dir)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/ytm", tags=["localmusic"])


@router.get("/library/status")
async def api_library_status():
    return INDEX.status()


@router.get("/library/browse")
async def api_library_browse(path: str = ""):
    return INDEX.browse(path)


@router.get("/library/search")
async def api_library_search(q: str = "", limit: int = 500):
    return INDEX.search(q, max(1, min(2000, int(limit or 500))))


@router.get("/library/all")
async def api_library_all():
    """The full lightweight index (id/name/folder) so the panel can search
    instantly client-side. Fetched once per library; re-fetched after re-index."""
    with INDEX.lock:
        return {"items": list(INDEX.entries), "count": len(INDEX.entries),
                "indexing": INDEX.indexing, "ready": INDEX.ready}


@router.post("/library/reindex")
async def api_library_reindex():
    """Force a fresh walk of the configured directory (the 'Re-scan' button).
    Normal startup/config changes reuse the cached index; this rebuilds it."""
    music_dir = str((ytmusic.CONFIG.get("local") or {}).get("music_dir", "") or "").strip()
    INDEX._start(Path(music_dir).resolve() if music_dir else None, force=True)
    return INDEX.status()


@router.get("/library/track/{tid}")
async def api_library_track(tid: str):
    return track_view(tid, deep=True)


def _fs_list(path: str) -> dict:
    """List directories on the host, for the in-panel folder picker. Unconfined
    (this is how you choose the music root); same LAN-only posture as the rest of
    Hexcast. Directories only - never file contents."""
    path = (path or "").strip()
    if not path:
        if os.name == "nt":
            drives = [{"name": f"{L}:\\", "path": f"{L}:\\"}
                      for L in string.ascii_uppercase if os.path.exists(f"{L}:\\")]
            return {"path": "", "parent": None, "dirs": drives}
        path = "/"
    try:
        base = Path(path).resolve()
    except Exception:
        return {"path": path, "parent": None, "dirs": [], "error": "bad path"}
    if not base.is_dir():
        return {"path": str(base), "parent": None, "dirs": [], "error": "not a directory"}
    dirs = []
    try:
        for c in sorted(base.iterdir(), key=lambda c: c.name.lower()):
            try:
                if c.is_dir() and not c.name.startswith("."):
                    dirs.append({"name": c.name, "path": str(c)})
            except OSError:
                pass
    except (OSError, PermissionError) as exc:
        return {"path": str(base), "parent": _fs_parent(base), "dirs": [], "error": str(exc)}
    return {"path": str(base), "parent": _fs_parent(base), "dirs": dirs}


def _fs_parent(base: Path):
    if base.parent == base:                       # drive root (C:\) or fs root (/)
        return "" if os.name == "nt" else None    # "" -> back to the drive list on Windows
    return str(base.parent)


@router.get("/fs/list")
async def api_fs_list(path: str = ""):
    return _fs_list(path)


def _native_pick_dir(initial: str = "") -> dict:
    """Open the host OS's native folder-picker dialog and return the chosen path.
    Runs in a short-lived subprocess (tkinter) so it never touches the server's
    event loop or thread state. Blocking - call it from an executor. The dialog
    appears on the machine running Hexcast, not on a remote panel's screen.
    Returns {"available": bool, "path": str}; available=False means no GUI/tk."""
    code = (
        "import tkinter, tkinter.filedialog as fd\n"
        "r=tkinter.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        "p=fd.askdirectory(initialdir=%r, title='Choose your music folder')\n"
        "print(p or '')\n" % (initial or "")
    )
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=300, creationflags=flags)
    except Exception as exc:
        _note(f"native folder picker failed to launch: {exc}")
        return {"available": False, "path": ""}
    if r.returncode != 0:
        _note(f"native folder picker unavailable: {(r.stderr or '').strip()[:200]}")
        return {"available": False, "path": ""}
    return {"available": True, "path": (r.stdout or "").strip()}


@router.post("/fs/pick")
async def api_fs_pick():
    """Pop the native OS folder dialog on the host and return the chosen folder."""
    initial = str((ytmusic.CONFIG.get("local") or {}).get("music_dir", "") or "")
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, _native_pick_dir, initial)
    return {"ok": bool(res.get("path")), **res}


def _queue_result() -> dict:
    return {"ok": True, "count": len(PLAYER.queue), "index": PLAYER.index}


@router.get("/queue")
@router.get("/queue/state")
async def api_queue_state():
    return PLAYER.state_view()


@router.get("/queue/items")
async def api_queue_items(offset: int = 0, limit: int = 200):
    return PLAYER.items_view(offset, limit)


@router.post("/queue/add")
async def api_queue_add(request: Request):
    body = await request.json()
    ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
    PLAYER.add([str(i) for i in ids], body.get("at"))
    await _after_queue_edit()
    return _queue_result()


@router.post("/queue/add-folder")
async def api_queue_add_folder(request: Request):
    """Add every track under a folder (relative to the music root) in one call -
    the workflow for 'queue the whole library' without shipping 15k ids."""
    body = await request.json()
    ids = INDEX.ids_in_folder(str(body.get("path", "") or ""), bool(body.get("recursive", True)))
    PLAYER.add(ids)
    await _after_queue_edit()
    return {"ok": True, "added": len(ids), "count": len(PLAYER.queue), "index": PLAYER.index}


@router.post("/queue/remove")
async def api_queue_remove(request: Request):
    body = await request.json()
    indices = body.get("indices")
    if indices is None and body.get("index") is not None:
        indices = [body["index"]]
    PLAYER.remove([int(i) for i in (indices or [])])
    await _after_queue_edit()
    return _queue_result()


@router.post("/queue/move")
async def api_queue_move(request: Request):
    body = await request.json()
    PLAYER.move(int(body.get("from", -1)), int(body.get("to", -1)))
    await _after_queue_edit()
    return _queue_result()


@router.post("/queue/clear")
async def api_queue_clear():
    PLAYER.clear()
    await _after_queue_edit()
    return _queue_result()


@router.post("/queue/play")
async def api_queue_play(request: Request):
    body = await request.json()
    PLAYER.play_index(int(body.get("index", 0)))
    await _after_transport()
    return _queue_result()


# ---- playlists ----------------------------------------------------------

async def _notify_playlists() -> None:
    await ytmusic.HUB.to_panel({"type": "playlists", "playlists": playlists_list()})


@router.get("/playlists")
async def api_playlists():
    return {"playlists": playlists_list()}


@router.post("/playlists/save")
async def api_playlist_save(request: Request):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    ids = body.get("ids")
    if ids is None:
        ids = list(PLAYER.queue)          # default: snapshot the current queue
    PLAYLISTS[name] = {"ids": [str(i) for i in ids], "created": time.time()}
    _save_playlists()
    await _notify_playlists()
    return {"ok": True, "name": name, "count": len(ids)}


@router.post("/playlists/load")
async def api_playlist_load(request: Request):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    pl = PLAYLISTS.get(name)
    if not pl:
        return JSONResponse({"error": "no such playlist"}, status_code=404)
    ids = [str(i) for i in (pl.get("ids") or [])]
    if str(body.get("mode", "replace")) == "append":
        PLAYER.add(ids)
    else:
        PLAYER.clear()
        PLAYER.add(ids)
        PLAYER.index = 0
    await _after_queue_edit()
    return {"ok": True, "count": len(PLAYER.queue)}


@router.post("/playlists/rename")
async def api_playlist_rename(request: Request):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    to = str(body.get("to", "")).strip()
    if name not in PLAYLISTS or not to:
        return JSONResponse({"error": "bad rename"}, status_code=400)
    PLAYLISTS[to] = PLAYLISTS.pop(name)
    _save_playlists()
    await _notify_playlists()
    return {"ok": True}


@router.post("/playlists/delete")
async def api_playlist_delete(request: Request):
    body = await request.json()
    PLAYLISTS.pop(str(body.get("name", "")).strip(), None)
    _save_playlists()
    await _notify_playlists()
    return {"ok": True}


_TRANSPARENT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d4944415478da6360000002000154a24f6f0000000049454e44ae426082"
)


@router.get("/localart/{tid}")
async def api_localart(tid: str):
    p = INDEX.path_for(tid)
    if p is None or not p.exists():
        return Response(status_code=404)
    art = ensure_art(p, tid)
    if art is None:
        return Response(status_code=404)
    return FileResponse(art, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/localfile/{tid}")
async def api_localfile(tid: str):
    """Serve the audio file. FileResponse honours HTTP Range automatically, which
    is what the overlay's <audio> needs for seeking (206 Partial Content)."""
    p = INDEX.path_for(tid)
    if p is None or not p.exists() or not p.is_file():
        return Response(status_code=404)
    ctype = CTYPES.get(p.suffix.lower(), "application/octet-stream")
    return FileResponse(p, media_type=ctype)


# --------------------------------------------------------------------------
# attach
# --------------------------------------------------------------------------

def attach_local(app, port: int = 4747) -> None:
    """Mount the local-music routes and kick off the library index. Called from
    ytmusic.attach_ytm() so both share the app, hub and config."""
    _load_meta_cache()
    app.include_router(router)
    reindex_from_config()
    print(f"  Music (local) lib:   {INDEX.status().get('root') or '(no directory set)'}", flush=True)
