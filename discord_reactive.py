"""
HexCast - Discord Reactive module
=================================

Drop this next to hexcast.py, then add two lines after the /media mount:

    from discord_reactive import attach_discord
    attach_discord(app, PORT)

Adds:
    http://localhost:4747/discord          -> control panel (connect + settings)
    http://localhost:4747/discord/overlay  -> voice-reactive overlay (OBS browser source)

Everyone in your current Discord voice channel appears in the overlay; their
image lights up while they speak and dims when silent - the same idea as
Discord Reactive Images / StreamKit, but self-hosted and themeable.

Speaking detection talks to the local Discord desktop client over its RPC
WebSocket (ports 6463-6472). No bot, no server-side Discord app: by default it
authorizes with the StreamKit client ID, which the desktop client already
trusts, so the only ceremony is one approval dialog inside Discord.

Security note: this file stores the RPC access token in
config/discord_secrets.json. HexCast has no auth, so treat that box as
trusted. The API never returns the token.
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
import websockets
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi import WebSocket, WebSocketDisconnect

# --------------------------------------------------------------------------
# paths / constants
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = Path(os.environ.get("HEXCAST_CONFIG_DIR", BASE_DIR / "config"))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = CONFIG_DIR / "discord.json"
SECRETS_PATH = CONFIG_DIR / "discord_secrets.json"

# Same env var hexcast.py uses, so uploads land under the mounted /media root.
MEDIA_ROOT = Path(os.getenv("SOUNDBOARD_MEDIA_DIR", str(BASE_DIR / "media"))).expanduser().resolve()
DISCORD_MEDIA_DIR = MEDIA_ROOT / "discord"
AVATAR_CACHE_DIR = DISCORD_MEDIA_DIR / "_avatars"

# The rpc scope only works for applications Discord has approved. StreamKit's
# client ID is approved and its token endpoint is public, which is why every
# "reactive images" tool uses it. Both can be overridden in config/discord.json
# if you have an approved app of your own.
STREAMKIT_CLIENT_ID = "207646673902501888"
STREAMKIT_TOKEN_URL = "https://streamkit.discord.com/overlay/token"
RPC_ORIGIN = "https://streamkit.discord.com"
RPC_PORTS = range(6463, 6473)

CDN = "https://cdn.discordapp.com"

# Everything we subscribe to for the active voice channel.
CHANNEL_EVENTS = (
    "VOICE_STATE_CREATE",
    "VOICE_STATE_UPDATE",
    "VOICE_STATE_DELETE",
    "SPEAKING_START",
    "SPEAKING_STOP",
)

IMAGE_EXTS = (".png", ".gif", ".webp")
SNOWFLAKE = re.compile(r"^\d{5,25}$")


def _read_static(name: str) -> str:
    path = STATIC_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Static file '{name}' not found at {path}. The Discord module needs "
            f"discord_panel.html and discord_overlay.html in ./static/ next to hexcast.py."
        )
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    # Blank means StreamKit. A custom application also needs a token_url that
    # can exchange the AUTHORIZE code for an access token.
    "client_id": "",
    "token_url": "",
    "overlay": {
        "layout": "row",          # row | column | grid
        "grid_columns": 3,
        "size": 128,
        "spacing": 18,
        "align": "center",        # start | center | end
        "show_names": True,
        "name_size": 14,
        "name_color": "#ffffff",
        # Applies to Discord avatars only; custom uploads always render
        # unclipped at their native aspect ratio.
        "shape": "circle",        # circle | rounded | square
        "dim": 0.4,               # brightness while silent
        # Users with a full idle+talking pair swap images to show speech, so
        # by default they skip the dim. Turn on to darken them like the rest.
        "dim_pairs": False,
        "desaturate": True,
        "bounce": True,
        "hide_muted": False,
        "mute_badge": True,
    },
    # user_id -> {"mode": "auto"|"avatar"|"custom", "size": 0, "offset_x": 0, "offset_y": 0}
    "users": {},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    raw = {}
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(cfg: dict) -> dict:
    merged = _deep_merge(DEFAULT_CONFIG, cfg)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


CONFIG = load_config()


def client_id() -> str:
    return (CONFIG.get("client_id") or "").strip() or STREAMKIT_CLIENT_ID


def token_url() -> str:
    return (CONFIG.get("token_url") or "").strip() or STREAMKIT_TOKEN_URL


# --------------------------------------------------------------------------
# token storage
# --------------------------------------------------------------------------

class Secrets:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        if SECRETS_PATH.exists():
            try:
                self.data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def save(self) -> None:
        SECRETS_PATH.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        try:
            os.chmod(SECRETS_PATH, 0o600)
        except Exception:
            pass

    @property
    def token(self) -> str:
        return self.data.get("access_token", "")

    def set_token(self, token: str) -> None:
        self.data["access_token"] = token
        self.data["client_id"] = client_id()
        self.data["authorized_at"] = time.time()
        self.save()

    def clear(self) -> None:
        for k in ("access_token", "client_id", "authorized_at"):
            self.data.pop(k, None)
        self.save()


SECRETS = Secrets()


# --------------------------------------------------------------------------
# runtime state
# --------------------------------------------------------------------------

class State:
    def __init__(self) -> None:
        self.connected = False        # RPC session up and authenticated
        self.discord_running = False  # an RPC port answered at all
        self.authorizing = False      # approval dialog is (probably) on screen
        self.declined = False         # user said no - don't nag until Connect
        self.self_user: dict | None = None
        self.channel: dict | None = None
        self.users: dict[str, dict] = {}
        self.last_error = ""
        self.log: list[str] = []

    def note(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.log.append(line)
        del self.log[:-60]
        print(f"[discord] {msg}", flush=True)

    def snapshot(self) -> dict:
        return {
            "connected": self.connected,
            "discord_running": self.discord_running,
            "authed": bool(SECRETS.token),
            "authorizing": self.authorizing,
            "declined": self.declined,
            "user": self.self_user,
            "channel": self.channel,
            "users": [public_user(u) for u in self.users.values()],
            "streamkit": not (CONFIG.get("client_id") or "").strip(),
            "last_error": self.last_error,
            "log": self.log[-25:],
        }


STATE = State()


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
        await self.to_overlay({"type": "config", "config": CONFIG})

    async def broadcast_status(self) -> None:
        await self.to_panel({"type": "status", "status": STATE.snapshot()})

    async def broadcast_roster(self) -> None:
        payload = {"type": "roster",
                   "channel": STATE.channel,
                   "users": [public_user(u) for u in STATE.users.values()]}
        await self.to_overlay(payload)
        await self.broadcast_status()


HUB = Hub()


# --------------------------------------------------------------------------
# user normalisation
# --------------------------------------------------------------------------

def _norm_user(vs: dict) -> dict:
    """Reduce an RPC voice-state object to what the overlay needs."""
    user = vs.get("user") or {}
    v = vs.get("voice_state") or {}
    return {
        "id": str(user.get("id") or ""),
        "username": user.get("username") or "",
        "name": vs.get("nick") or user.get("global_name") or user.get("username") or "",
        "avatar": user.get("avatar") or "",
        "bot": bool(user.get("bot")),
        "mute": bool(v.get("mute") or v.get("self_mute") or v.get("suppress")),
        "deaf": bool(v.get("deaf") or v.get("self_deaf")),
        "speaking": False,
    }


def _custom_images(uid: str) -> dict:
    """URLs for uploaded idle/talking images, if any. Served by the existing
    /media mount; the mtime query busts OBS's cache after a re-upload."""
    out: dict[str, str] = {}
    d = DISCORD_MEDIA_DIR / uid
    for which in ("idle", "talking"):
        for ext in IMAGE_EXTS:
            p = d / f"{which}{ext}"
            if p.exists():
                out[which] = f"/media/discord/{uid}/{which}{ext}?v={int(p.stat().st_mtime)}"
                break
    return out


def public_user(u: dict) -> dict:
    uid = u["id"]
    out = dict(u)
    out["avatar_url"] = f"/discord/avatar/{uid}" + (f"?h={u['avatar']}" if u.get("avatar") else "")
    out["images"] = _custom_images(uid)
    return out


# --------------------------------------------------------------------------
# Discord RPC client
# --------------------------------------------------------------------------

class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code
        self.message = message


class RpcClient:
    """One long-lived session against the local Discord client's RPC socket.

    A single read loop resolves command responses (matched by nonce) and
    dispatches events. Commands must never be awaited from inside an event
    handler - that would deadlock the read loop - so handlers that need to
    talk back spawn a task.
    """

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.lock = asyncio.Lock()
        self.started = False
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.ready = asyncio.Event()
        self.sub_channel_id = ""

    # ---- supervisor ------------------------------------------------------

    async def restart(self) -> None:
        async with self.lock:
            await self._stop_task()
            STATE.declined = False
            self.stop = asyncio.Event()
            self.task = asyncio.create_task(self._run(self.stop))
            self.started = True

    async def shutdown(self) -> None:
        async with self.lock:
            await self._stop_task()
            self.started = False
        await HUB.broadcast_status()

    async def _stop_task(self) -> None:
        self.stop.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        STATE.connected = False
        STATE.discord_running = False
        STATE.channel = None
        STATE.users.clear()

    async def ensure_started(self) -> None:
        if not self.started:
            await self.restart()

    # ---- connection loop -------------------------------------------------

    async def _run(self, stop: asyncio.Event) -> None:
        backoff = 2
        was_down = False
        while not stop.is_set():
            ws = await self._connect_any_port()
            if ws is None:
                STATE.connected = False
                if STATE.discord_running or not was_down:
                    STATE.discord_running = False
                    STATE.last_error = "Discord desktop client is not running"
                    STATE.note("Discord client not found on ports 6463-6472, retrying")
                    await HUB.broadcast_status()
                was_down = True
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)
                continue

            backoff = 2
            was_down = False
            STATE.discord_running = True
            try:
                await self._session(ws, stop)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                STATE.last_error = str(exc)
                STATE.note(f"RPC session dropped: {exc}")
            finally:
                try:
                    await ws.close()
                except Exception:
                    pass
                self.ws = None
                STATE.connected = False
                STATE.channel = None
                if STATE.users:
                    STATE.users.clear()
                    await HUB.broadcast_roster()
                else:
                    await HUB.broadcast_status()

            if STATE.declined:
                # The user closed or denied the approval dialog. Reconnecting
                # would pop it straight back up, so park until Connect is
                # clicked in the panel.
                STATE.note("waiting - click Connect in the panel to ask Discord again")
                return
            if not stop.is_set():
                await asyncio.sleep(2)

    async def _connect_any_port(self):
        """Discord picks the first free port in 6463-6472, so scan them all.
        The client rejects connections whose Origin it doesn't trust; the
        StreamKit origin is on the allow-list of the StreamKit client ID."""
        for port in RPC_PORTS:
            if self.stop.is_set():
                return None
            uri = f"ws://127.0.0.1:{port}/?v=1&client_id={client_id()}&encoding=json"
            try:
                return await websockets.connect(
                    uri, origin=RPC_ORIGIN, open_timeout=2, max_size=2 ** 22)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
        return None

    async def _session(self, ws, stop: asyncio.Event) -> None:
        self.ws = ws
        self.pending = {}
        self.ready = asyncio.Event()
        self.sub_channel_id = ""

        reader = asyncio.create_task(self._read_loop(ws))
        stopper = asyncio.create_task(stop.wait())
        handshake: asyncio.Task | None = None
        ready_wait = asyncio.create_task(self.ready.wait())
        try:
            done, _ = await asyncio.wait(
                {reader, stopper, ready_wait}, timeout=10,
                return_when=asyncio.FIRST_COMPLETED)
            if ready_wait not in done:
                raise RuntimeError("Discord RPC sent no READY - wrong port or client ID")

            handshake = asyncio.create_task(self._handshake())
            done, _ = await asyncio.wait({reader, stopper, handshake},
                                         return_when=asyncio.FIRST_COMPLETED)
            if handshake in done:
                # Surface a handshake failure (bad token, declined auth)
                # instead of idling on a half-set-up session.
                if handshake.exception():
                    raise handshake.exception()
                # Handshake done - hold the session until the socket drops
                # or we're told to stop.
                await asyncio.wait({reader, stopper},
                                   return_when=asyncio.FIRST_COMPLETED)
            if reader.done() and not stop.is_set():
                exc = reader.exception()
                if exc:
                    raise exc
                raise RuntimeError("Discord closed the RPC connection")
        finally:
            for t in (reader, stopper, handshake, ready_wait):
                if t and not t.done():
                    t.cancel()
            for fut in self.pending.values():
                if not fut.done():
                    fut.cancel()

    async def _read_loop(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            nonce = msg.get("nonce")
            if nonce and nonce in self.pending:
                fut = self.pending[nonce]
                if not fut.done():
                    fut.set_result(msg)
                continue
            if msg.get("cmd") == "DISPATCH":
                try:
                    await self._on_event(msg.get("evt") or "", msg.get("data") or {})
                except Exception as exc:
                    STATE.note(f"event handler failed: {exc}")

    async def _request(self, cmd: str, args: dict | None = None,
                       evt: str | None = None, timeout: float = 15) -> dict:
        payload: dict[str, Any] = {"cmd": cmd, "nonce": secrets.token_hex(8)}
        if args is not None:
            payload["args"] = args
        if evt:
            payload["evt"] = evt
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[payload["nonce"]] = fut
        try:
            await self.ws.send(json.dumps(payload))
            msg = await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(payload["nonce"], None)
        if msg.get("evt") == "ERROR":
            data = msg.get("data") or {}
            raise RpcError(int(data.get("code") or 0), data.get("message") or "unknown error")
        return msg.get("data") or {}

    # ---- handshake -------------------------------------------------------

    async def _handshake(self) -> None:
        await self._authenticate()
        STATE.connected = True
        STATE.last_error = ""
        await HUB.broadcast_status()
        await self._request("SUBSCRIBE", {}, evt="VOICE_CHANNEL_SELECT")
        await self._refresh_channel()

    async def _authenticate(self) -> None:
        if SECRETS.token:
            try:
                data = await self._request("AUTHENTICATE", {"access_token": SECRETS.token})
                STATE.self_user = self._self_from(data)
                STATE.note(f"authenticated as {STATE.self_user.get('name', '?')} (cached token)")
                return
            except RpcError:
                STATE.note("cached token rejected - asking Discord for approval again")
                SECRETS.clear()

        STATE.authorizing = True
        STATE.note("approval dialog sent to Discord - click Authorize there")
        await HUB.broadcast_status()
        try:
            data = await self._request(
                "AUTHORIZE",
                {"client_id": client_id(), "scopes": ["rpc"], "prompt": "none"},
                timeout=120)
        except (RpcError, asyncio.TimeoutError) as exc:
            STATE.declined = True
            STATE.last_error = "authorization declined or timed out"
            STATE.note(f"authorization did not complete: {exc}")
            raise RuntimeError("authorization declined") from exc
        finally:
            STATE.authorizing = False
            await HUB.broadcast_status()

        code = data.get("code") or ""
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(token_url(), json={"code": code})
        r.raise_for_status()
        token = r.json().get("access_token") or ""
        if not token:
            raise RuntimeError(f"token endpoint {token_url()} returned no access_token")
        SECRETS.set_token(token)

        data = await self._request("AUTHENTICATE", {"access_token": token})
        STATE.self_user = self._self_from(data)
        STATE.note(f"authorized and authenticated as {STATE.self_user.get('name', '?')}")

    @staticmethod
    def _self_from(auth_data: dict) -> dict:
        u = auth_data.get("user") or {}
        return {
            "id": str(u.get("id") or ""),
            "name": u.get("global_name") or u.get("username") or "",
            "username": u.get("username") or "",
            "avatar": u.get("avatar") or "",
        }

    # ---- voice channel ---------------------------------------------------

    async def _refresh_channel(self) -> None:
        """Ask which voice channel is selected and (re)subscribe to it."""
        channel = await self._request("GET_SELECTED_VOICE_CHANNEL")
        await self._switch_channel(channel or None)

    async def _switch_channel(self, channel: dict | None) -> None:
        new_id = str((channel or {}).get("id") or "")
        if self.sub_channel_id and self.sub_channel_id != new_id:
            for evt in CHANNEL_EVENTS:
                try:
                    await self._request("UNSUBSCRIBE", {"channel_id": self.sub_channel_id}, evt=evt)
                except Exception:
                    pass
            self.sub_channel_id = ""

        STATE.users.clear()
        if not new_id:
            STATE.channel = None
            STATE.note("not in a voice channel")
            await HUB.broadcast_roster()
            return

        if self.sub_channel_id != new_id:
            for evt in CHANNEL_EVENTS:
                await self._request("SUBSCRIBE", {"channel_id": new_id}, evt=evt)
            self.sub_channel_id = new_id

        STATE.channel = {"id": new_id, "name": (channel or {}).get("name") or ""}
        for vs in (channel or {}).get("voice_states") or []:
            u = _norm_user(vs)
            if u["id"]:
                STATE.users[u["id"]] = u
        STATE.note(f"voice channel: {STATE.channel['name']} ({len(STATE.users)} in it)")
        await HUB.broadcast_roster()

    # ---- events ----------------------------------------------------------

    async def _on_event(self, evt: str, data: dict) -> None:
        if evt == "READY":
            self.ready.set()
            return

        if evt == "VOICE_CHANNEL_SELECT":
            # Needs a round-trip (GET_SELECTED_VOICE_CHANNEL) - do it in a task
            # so this read loop stays free to deliver the response.
            asyncio.create_task(self._safe_refresh())
            return

        if evt in ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE"):
            u = _norm_user(data)
            if not u["id"]:
                return
            old = STATE.users.get(u["id"])
            if old:
                u["speaking"] = old["speaking"]
            STATE.users[u["id"]] = u
            await HUB.to_overlay({"type": "user", "user": public_user(u)})
            await HUB.broadcast_status()
            return

        if evt == "VOICE_STATE_DELETE":
            uid = str((data.get("user") or {}).get("id") or "")
            if uid and STATE.users.pop(uid, None):
                await HUB.to_overlay({"type": "leave", "id": uid})
                await HUB.broadcast_status()
            return

        if evt in ("SPEAKING_START", "SPEAKING_STOP"):
            uid = str(data.get("user_id") or "")
            speaking = evt == "SPEAKING_START"
            u = STATE.users.get(uid)
            if u and u["speaking"] != speaking:
                u["speaking"] = speaking
                await HUB.to_overlay({"type": "speaking", "id": uid, "speaking": speaking})
                await HUB.to_panel({"type": "speaking", "id": uid, "speaking": speaking})

    async def _safe_refresh(self) -> None:
        try:
            await self._refresh_channel()
        except Exception as exc:
            STATE.note(f"could not follow the channel change: {exc}")


RPC = RpcClient()


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/discord", tags=["discord"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    await RPC.ensure_started()
    return HTMLResponse(_read_static("discord_panel.html"))


@router.get("/overlay", response_class=HTMLResponse)
async def overlay():
    await RPC.ensure_started()
    return HTMLResponse(_read_static("discord_overlay.html"))


@router.get("/avatar/{user_id}")
async def avatar_proxy(user_id: str, h: str = ""):
    """Fetch Discord CDN avatars server-side, like /ytm/art does for album art.
    Same-origin means the overlay can canvas-sample them, and the disk cache
    keeps things instant on OBS reloads."""
    if not SNOWFLAKE.match(user_id):
        return Response(status_code=400)
    avatar_hash = h or (STATE.users.get(user_id) or {}).get("avatar") or ""
    if avatar_hash and not re.match(r"^[a-zA-Z0-9_]{4,64}$", avatar_hash):
        return Response(status_code=400)

    cache = AVATAR_CACHE_DIR / f"{user_id}_{avatar_hash or 'default'}.png"
    if cache.exists():
        return Response(cache.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    if avatar_hash:
        url = f"{CDN}/avatars/{user_id}/{avatar_hash}.png?size=256"
    else:
        url = f"{CDN}/embed/avatars/{(int(user_id) >> 22) % 6}.png"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
            r = await c.get(url)
    except Exception:
        return Response(status_code=502)
    if r.status_code != 200 or not r.content:
        return Response(status_code=404)

    try:
        AVATAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(r.content)
    except OSError:
        pass
    return Response(r.content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/status")
async def api_status():
    return STATE.snapshot()


@router.get("/api/config")
async def api_get_config():
    return CONFIG


@router.post("/api/config")
async def api_set_config(request: Request):
    global CONFIG
    incoming = await request.json()
    old_cid = (CONFIG.get("client_id") or "").strip()
    CONFIG = save_config(_deep_merge(CONFIG, incoming))
    await HUB.broadcast_config()
    await HUB.broadcast_status()
    if (CONFIG.get("client_id") or "").strip() != old_cid:
        # A different application means the cached token is for the wrong app.
        SECRETS.clear()
        await RPC.restart()
    return {"ok": True, "config": CONFIG}


@router.post("/api/connect")
async def api_connect():
    await RPC.restart()
    return {"ok": True}


@router.post("/api/disconnect")
async def api_disconnect():
    await RPC.shutdown()
    STATE.note("disconnected - the overlay stays blank until Connect")
    return {"ok": True}


@router.post("/api/forget")
async def api_forget():
    SECRETS.clear()
    STATE.note("authorization forgotten - Discord will ask again on connect")
    await RPC.restart()
    return {"ok": True}


@router.post("/api/upload/{user_id}/{which}")
async def api_upload(user_id: str, which: str, file: UploadFile = File(...)):
    if not SNOWFLAKE.match(user_id):
        return JSONResponse({"error": "bad user id"}, status_code=400)
    if which not in ("idle", "talking"):
        return JSONResponse({"error": "which must be 'idle' or 'talking'"}, status_code=400)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXTS:
        return JSONResponse({"error": f"use png, gif or webp (got {ext or 'no extension'})"},
                            status_code=400)

    content = await file.read()
    d = DISCORD_MEDIA_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    # One image per slot: replace whatever extension was there before.
    for old_ext in IMAGE_EXTS:
        if old_ext != ext:
            (d / f"{which}{old_ext}").unlink(missing_ok=True)
    (d / f"{which}{ext}").write_bytes(content)

    await _push_user_images(user_id)
    return {"ok": True, "images": _custom_images(user_id)}


@router.post("/api/images/delete")
async def api_delete_image(request: Request):
    body = await request.json()
    user_id = str(body.get("user_id") or "")
    which = body.get("which") or ""
    if not SNOWFLAKE.match(user_id) or which not in ("idle", "talking"):
        return JSONResponse({"error": "user_id and which ('idle'/'talking') required"},
                            status_code=400)
    d = DISCORD_MEDIA_DIR / user_id
    for ext in IMAGE_EXTS:
        (d / f"{which}{ext}").unlink(missing_ok=True)
    await _push_user_images(user_id)
    return {"ok": True, "images": _custom_images(user_id)}


async def _push_user_images(user_id: str) -> None:
    """After an upload/delete, resend that user so open overlays repaint."""
    u = STATE.users.get(user_id)
    if u:
        await HUB.to_overlay({"type": "user", "user": public_user(u)})
    await HUB.broadcast_status()


@router.post("/api/test/user")
async def api_test_user(request: Request):
    """Inject or remove a fake participant so the overlay can be styled and
    placed in OBS without being in a call. Toggles speaking on repeat calls."""
    body = await request.json() if await request.body() else {}
    if body.get("remove"):
        removed = [uid for uid in list(STATE.users) if uid.startswith("9999")]
        for uid in removed:
            STATE.users.pop(uid, None)
            await HUB.to_overlay({"type": "leave", "id": uid})
        await HUB.broadcast_status()
        return {"ok": True, "removed": len(removed)}

    uid = str(body.get("id") or "999900000000000001")
    u = STATE.users.get(uid)
    if u is None:
        u = {"id": uid, "username": "testuser", "name": body.get("name") or "Test User",
             "avatar": "", "bot": False, "mute": False, "deaf": False, "speaking": True}
        STATE.users[uid] = u
        await HUB.to_overlay({"type": "user", "user": public_user(u)})
    else:
        u["speaking"] = not u["speaking"]
    await HUB.to_overlay({"type": "speaking", "id": uid, "speaking": u["speaking"]})
    await HUB.broadcast_status()
    return {"ok": True, "id": uid, "speaking": u["speaking"]}


@router.websocket("/ws/overlay")
async def ws_overlay(ws: WebSocket):
    await ws.accept()
    HUB.overlay.add(ws)
    await RPC.ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        await ws.send_text(json.dumps({
            "type": "roster",
            "channel": STATE.channel,
            "users": [public_user(u) for u in STATE.users.values()],
        }))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.overlay.discard(ws)


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    HUB.panel.add(ws)
    await RPC.ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "status", "status": STATE.snapshot()}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.panel.discard(ws)


# --------------------------------------------------------------------------
# attach
# --------------------------------------------------------------------------

async def start_discord() -> None:
    """Call from inside hexcast's lifespan startup."""
    await RPC.ensure_started()


async def stop_discord() -> None:
    """Call from inside hexcast's lifespan shutdown."""
    await RPC.shutdown()


def attach_discord(app, port: int = 4747) -> None:
    """Mount the Discord routes onto an existing FastAPI app.

    hexcast.py builds its app with FastAPI(lifespan=...), so Starlette ignores
    add_event_handler("startup"). The RPC connection therefore starts lazily on
    the first panel or overlay request. Call start_discord()/stop_discord()
    from that lifespan to connect at boot instead.
    """
    app.include_router(router)
    print(f"  Discord panel:       http://localhost:{port}/discord", flush=True)
    print(f"  Discord overlay:     http://localhost:{port}/discord/overlay", flush=True)
