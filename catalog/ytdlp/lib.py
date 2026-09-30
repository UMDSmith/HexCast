"""
HexCast - yt-dlp helpers (library plugin)
=========================================

The shared YouTube / Twitch link resolver used by the Clips and Music plugins. It has no
tab and no routes: it finds yt-dlp (the copy in this Python environment, else one on PATH),
runs it as a subprocess, retries with the linked browser sign-in when YouTube refuses an
anonymous lookup, and tests that sign-in for the "Link" buttons.

Clips and Music `require` this plugin, so Hexcast installs and starts it first; they import
it as `hexcast_plugins.ytdlp.lib`. Whatever is specific to one of them (Clips' queue and
settings, the "Update yt-dlp" button, Music's own saved browser link) stays in that plugin.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import shutil
import sys


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


def js_runtime() -> tuple[str, list[str]]:
    """The JavaScript runtime yt-dlp can use for YouTube's challenges, and the
    args to select it. yt-dlp only turns on Deno by default; Node and Bun work
    too but have to be named with --js-runtimes."""
    if shutil.which("deno"):
        return "deno", []
    for rt in ("node", "bun"):
        if shutil.which(rt):
            return rt, ["--js-runtimes", rt]
    return "", []


def has_solver() -> bool:
    """yt-dlp's challenge-solver scripts (the yt-dlp-ejs package)."""
    return importlib.util.find_spec("yt_dlp_ejs") is not None


def signin_readiness() -> dict:
    """What a signed-in YouTube lookup needs besides the sign-in itself. Shown
    on the Music and Clips sign-in cards."""
    return {"js_runtime": js_runtime()[0], "solver": has_solver()}


_DENO_HINT = "install Deno (winget install DenoLand.Deno), then restart Hexcast"
_SOLVER_HINT = "run: python -m pip install -U yt-dlp yt-dlp-ejs (or click Update yt-dlp on the Clips page)"


# --------------------------------------------------------------------------
# yt-dlp - one consistent mechanism: subprocess `python -m yt_dlp`, JSON via -j
# --------------------------------------------------------------------------

async def ytdlp_run(*args: str, timeout: float = 120, browser: str = "") -> bytes:
    """One yt-dlp call. `browser` adds --cookies-from-browser for that browser.
    Shared with ytmusic.py (music videos), which has its own, separate link."""
    cmd = _ytdlp_cmd()
    if cmd is None:
        raise RuntimeError("yt-dlp not found - install it in this environment "
                           "(pip install yt-dlp) or put it on PATH")
    extra = ["--cookies-from-browser", browser] if browser else []
    extra += js_runtime()[1]
    proc = await asyncio.create_subprocess_exec(
        *cmd, "--no-warnings", "--no-playlist", *extra, *args,
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


# YouTube refusals that a signed-in session can get past.
_NEEDS_LOGIN = re.compile(r"sign in|not a bot|age.restrict|confirm your age|members.only|"
                          r"private video|login required|use --cookies", re.I)


def needs_login(err: str) -> bool:
    return bool(_NEEDS_LOGIN.search(err or ""))


async def ytdlp_with_login(*args: str, timeout: float = 120, browser: str = "") -> bytes:
    """Anonymous first; retry with the linked browser only if YouTube refused
    for a reason a login can fix. Signed-in lookups go through a different
    YouTube client that needs extra JavaScript tooling, so using the login
    for everything would make ordinary lookups fail."""
    try:
        return await ytdlp_run(*args, timeout=timeout)
    except RuntimeError as exc:
        if not browser or not needs_login(str(exc)):
            raise
        try:
            return await ytdlp_run(*args, timeout=timeout, browser=browser)
        except RuntimeError as exc2:
            raise RuntimeError(f"{exc} (also failed with your linked {browser} "
                               f"sign-in: {exc2})") from exc2


VALID_COOKIE_BROWSERS = ("firefox", "chrome")
_LINK_TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"   # "Me at the zoo"


async def test_login_link(browser: str) -> dict:
    """What the Link buttons run before saving anything: can yt-dlp read this
    browser's cookies, is there a YouTube sign-in among them, and does a
    lookup with them work right now. Returns {ok, message}; ok means the link
    may be saved."""
    cmd = _ytdlp_cmd()
    if cmd is None:
        return {"ok": False, "message": "yt-dlp is not installed"}
    proc = await asyncio.create_subprocess_exec(
        *cmd, "-v", "--no-playlist", "--cookies-from-browser", browser, *js_runtime()[1],
        "--skip-download", "--print", "id", _LINK_TEST_URL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), 90)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "message": "Timed out reading the browser session."}
    text = (err or b"").decode("utf-8", errors="replace")
    last_error = next((ln for ln in reversed(text.splitlines()) if "ERROR" in ln), "")
    last_error = last_error.replace("ERROR: ", "").strip()
    m = re.search(r"Extracted (\d+) cookies", text)
    if not m:
        hint = (" Chrome locks its cookies while it's running - close Chrome "
                "completely and try again, or use Firefox.") if browser == "chrome" else ""
        return {"ok": False, "message": f"Couldn't read {browser}'s cookies: "
                f"{last_error or 'unknown error'}.{hint}"}
    if "Found YouTube account cookies" not in text:
        return {"ok": False, "message": f"Read {m.group(1)} cookies from {browser}, but "
                f"none of them are a YouTube sign-in. Sign in to YouTube in {browser} "
                "first, then click Link again."}
    if proc.returncode == 0:
        return {"ok": True, "message": f"Linked to your {browser} YouTube sign-in. "
                "A test lookup with it worked."}
    # Signed-in lookups need YouTube's JavaScript challenge solved; name the
    # missing piece rather than passing on yt-dlp's raw error.
    runtime, solver = js_runtime()[0], has_solver()
    if not runtime:
        fix = f"No JavaScript runtime was found - {_DENO_HINT}, and click Link again."
    elif not solver:
        fix = f"yt-dlp's challenge-solver scripts are missing - {_SOLVER_HINT}, then click Link again."
    else:
        fix = f"yt-dlp said: {last_error[:160]}. Try updating yt-dlp: python -m pip install -U yt-dlp yt-dlp-ejs (or Update yt-dlp on the Clips page)."
    return {"ok": True, "message": f"Linked to your {browser} YouTube sign-in, but a "
            f"test lookup with it failed. {fix} Until then lookups stay anonymous, "
            "which only matters for age-restricted or bot-checked videos."}
