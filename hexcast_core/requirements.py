"""Plugin dependencies: read a requirements.txt, tell whether this Python already has
everything in it, and remember that it was checked.

pip does the installing. This module only lets Hexcast skip pip when there is nothing
to do (an upgrade of an existing install, or every ordinary restart), so a plugin loads
instantly and works offline once its packages are in.

Only the plain `name>=version` form is judged here. Anything fancier (markers, URLs,
`~=`, `!=`, options) counts as "not sure", which just means pip gets to look at it.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_SPEC = re.compile(r"^(>=|<=|==|>|<)\s*([0-9][0-9A-Za-z.]*)$")


@dataclass(frozen=True)
class Requirement:
    name: str
    specs: tuple[tuple[str, str], ...]
    sure: bool          # False: something this module does not judge - leave it to pip
    text: str


def _digits(v: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", v))


def _cmp(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def parse(text: str) -> list[Requirement]:
    """The requirements in a requirements.txt (comments and blank lines skipped)."""
    out: list[Requirement] = []
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].split("\t#", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        m = None if line.startswith("-") else _LINE.match(line)
        if m is None:                       # an option (-r, -e, --index-url ...) or a URL
            out.append(Requirement(line, (), False, line))
            continue
        name, rest = m.group(1), m.group(3).strip()
        if ";" in rest or "@" in rest:      # environment marker or direct reference
            out.append(Requirement(name, (), False, line))
            continue
        specs: list[tuple[str, str]] = []
        sure = True
        for part in [p.strip() for p in rest.split(",") if p.strip()]:
            sm = _SPEC.match(part)
            if sm is None:
                sure = False
                break
            specs.append((sm.group(1), sm.group(2)))
        out.append(Requirement(name, tuple(specs), sure, line))
    return out


def satisfied(req: Requirement) -> bool:
    """True only when the package is installed and certainly meets the requirement."""
    if not req.sure:
        return False
    try:
        have = _digits(md.version(req.name))
    except md.PackageNotFoundError:
        return False
    except Exception:                       # a broken metadata folder: let pip sort it out
        return False
    for op, ver in req.specs:
        c = _cmp(have, _digits(ver))
        ok = {">=": c >= 0, "<=": c <= 0, "==": c == 0, ">": c > 0, "<": c < 0}[op]
        if not ok:
            return False
    return True


# ---- "already checked" memory --------------------------------------------------
# One small file per plugin inside the Python environment itself (sys.prefix), so a
# fresh virtualenv forgets it and everything is re-checked - which is exactly right.

def _lock_dir() -> Path:
    return Path(sys.prefix) / ".hexcast-plugin-deps"


def digest(text: str) -> str:
    return hashlib.sha256((text + "\n" + sys.version.split()[0]).encode("utf-8")).hexdigest()[:24]


def _read_lock(pid: str) -> str | None:
    try:
        return (_lock_dir() / pid).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def mark_installed(pid: str, text: str) -> None:
    try:
        d = _lock_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / pid).write_text(digest(text), encoding="utf-8")
    except OSError:
        pass                                # no lock = it is simply checked again next time


def forget(pid: str) -> None:
    try:
        (_lock_dir() / pid).unlink()
    except OSError:
        pass


def deps_ok(pid: str, req_file: Path | None) -> bool:
    """Are the plugin's packages in place? (Cheap: a lock hit, else one look at each package.)"""
    if req_file is None or not req_file.is_file():
        return True
    try:
        text = req_file.read_text(encoding="utf-8")
    except OSError:
        return True
    if _read_lock(pid) == digest(text):
        return True
    if all(satisfied(r) for r in parse(text)):
        mark_installed(pid, text)
        return True
    return False
