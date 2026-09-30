"""Static files that browsers re-check every time.

Starlette's StaticFiles sends Last-Modified / ETag but no Cache-Control, so a browser (or an
OBS browser source) is free to reuse a copy for hours. After an upgrade that meant the old
top bar - eight hard-wired tabs - kept showing until a hard refresh. `no-cache` does not mean
"do not store": the browser keeps the file but asks first, and Hexcast answers "not modified"
(a tiny 304) unless it changed. Fine for the handful of small files these mounts serve.
"""

from __future__ import annotations

from starlette.staticfiles import StaticFiles


class RevalidatingStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response
