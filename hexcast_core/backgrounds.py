"""The shared library of overlay background images.

Chat bubbles, alert cards and countdown faces can all use a picture as their background.
The pictures live in media/overlays/ (served at /media/overlays/...), and any plugin's
panel can list, upload and delete them through these three routes - so a background
uploaded from one plugin is there in every other, whichever plugins are installed.

(The Twitch plugin still answers the same calls at /twitch/api/backgrounds, unchanged.)
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

BG_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".apng"}
MAX_BYTES = 25 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    """A safe, flat file name for an uploaded picture."""
    stem = _SAFE.sub("_", Path(name).stem).strip("._-") or "background"
    return stem[:60] + Path(name).suffix.lower()


def build_router(media_dir: Path) -> APIRouter:
    folder = Path(media_dir) / "overlays"
    router = APIRouter(prefix="/api/backgrounds", tags=["backgrounds"])

    def listing() -> list[dict]:
        folder.mkdir(parents=True, exist_ok=True)
        return [{"name": p.name, "url": f"/media/overlays/{p.name}"}
                for p in sorted(folder.glob("*")) if p.is_file() and p.suffix.lower() in BG_IMAGE_EXTS]

    @router.get("")
    async def backgrounds_list():
        return {"backgrounds": listing()}

    @router.post("")
    async def backgrounds_upload(file: UploadFile = File(...)):
        ext = Path(file.filename or "").suffix.lower()
        if ext not in BG_IMAGE_EXTS:
            return JSONResponse({"error": f"use png, jpg, gif, webp or apng (got {ext or 'no extension'})"},
                                status_code=400)
        content = await file.read()
        if len(content) > MAX_BYTES:
            return JSONResponse({"error": "file too big (max 25 MB)"}, status_code=400)
        name = safe_name(file.filename or "background")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
        return {"ok": True, "name": name, "url": f"/media/overlays/{name}", "backgrounds": listing()}

    @router.post("/delete")
    async def backgrounds_delete(request: Request):
        body = await request.json()
        name = Path(str(body.get("name") or "")).name          # .name strips any path
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        (folder / name).unlink(missing_ok=True)
        return {"ok": True, "backgrounds": listing()}

    return router
