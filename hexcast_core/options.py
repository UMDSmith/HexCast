"""Master options: the settings that belong to Hexcast as a whole (the gear in the top bar, /options).

Each option is one entry in OPTIONS: its key, a default, how to validate what arrives, and how to
store it. To add a global option, add an entry here (and a row on static/options.html) - the API
(GET/POST /api/options) validates and saves it with no other change.

They live in config/plugins.json next to the plugin host's other settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .host import PluginHost
from .versions import DEFAULT_UPSTREAM, normalize_upstream


class OptionError(ValueError):
    """An option value that is not acceptable; str(exc) is fit to show a person."""


@dataclass(frozen=True)
class Option:
    key: str
    default: Any
    read: Callable[[PluginHost], Any]                 # the value in force
    validate: Callable[[Any], Any]                    # raw value -> clean value, or OptionError
    write: Callable[[PluginHost, Any], None]          # store it (and apply it)
    locked: Callable[[PluginHost], str | None] = lambda host: None     # why it cannot be changed right now


def _boolean(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    raise OptionError("must be true or false")


def _repo(v: Any) -> str:
    if not isinstance(v, str):
        raise OptionError("must be text like owner/name")
    v = v.strip()
    # accept a pasted GitHub address too
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if v.lower().startswith(prefix):
            v = v[len(prefix):]
            break
    v = v.removesuffix(".git").strip("/")
    cfg, why = normalize_upstream({"repo": v})
    if why or cfg is None or cfg["repo"] != v:
        raise OptionError("must look like owner/name (for example UMDSmith/hexcast)")
    return v


def _branch(v: Any) -> str:
    if not isinstance(v, str):
        raise OptionError("must be a branch name such as main")
    v = v.strip()
    cfg, why = normalize_upstream({"branch": v})
    if why or cfg is None or cfg["branch"] != v:
        raise OptionError("must be a branch name such as main")
    return v


def _set_check(host: PluginHost, on: bool) -> None:
    host.settings.set_check_updates(on)


def _set_repo(host: PluginHost, repo: str) -> None:
    host.settings.set_upstream(repo, _current_branch(host))


def _set_branch(host: PluginHost, branch: str) -> None:
    host.settings.set_upstream(_current_repo(host), branch)


def _current_repo(host: PluginHost) -> str:
    return (host.settings.upstream or DEFAULT_UPSTREAM)["repo"]


def _current_branch(host: PluginHost) -> str:
    return (host.settings.upstream or DEFAULT_UPSTREAM)["branch"]


def _env_lock(host: PluginHost) -> str | None:
    return "HEXCAST_NO_UPSTREAM is set in the environment, so Hexcast never checks GitHub" if host.env_locked() else None


OPTIONS: dict[str, Option] = {o.key: o for o in (
    Option("check_updates", True, lambda h: h.settings.check_updates and h.settings.upstream is not None,
           _boolean, _set_check, _env_lock),
    Option("upstream_repo", DEFAULT_UPSTREAM["repo"], _current_repo, _repo, _set_repo),
    Option("upstream_branch", DEFAULT_UPSTREAM["branch"], _current_branch, _branch, _set_branch),
)}


def read_all(host: PluginHost) -> dict:
    return {
        "options": {k: o.read(host) for k, o in OPTIONS.items()},
        "defaults": {k: o.default for k, o in OPTIONS.items()},
        "locked": {k: why for k, o in OPTIONS.items() if (why := o.locked(host))},
        "updates": host.upstream.status(),
    }


def apply(host: PluginHost, changes: Any) -> dict:
    """Validate every change first and store them only if all are fine. Returns read_all().
    Raises OptionError with .errors = {key: message}."""
    if not isinstance(changes, dict) or not changes:
        raise _errors({"": "send an object such as {\"check_updates\": false}"})
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, raw in changes.items():
        opt = OPTIONS.get(key)
        if opt is None:
            errors[str(key)[:40]] = "unknown option"
            continue
        why = opt.locked(host)
        if why:
            errors[key] = why
            continue
        try:
            clean[key] = opt.validate(raw)
        except OptionError as exc:
            errors[key] = str(exc)
    if errors:
        raise _errors(errors)
    # a repo / branch change must not turn checking back on by accident: only `check_updates` does that
    for key, value in clean.items():
        OPTIONS[key].write(host, value)
    host.reload_upstream()
    return read_all(host)


def _errors(errors: dict[str, str]) -> OptionError:
    exc = OptionError("; ".join(f"{k}: {m}" if k else m for k, m in errors.items()))
    exc.errors = errors                                # type: ignore[attr-defined]
    return exc
