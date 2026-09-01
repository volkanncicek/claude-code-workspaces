"""Atomic JSON read/write under `~/.ccw/`. Read returns `None` on any failure; caller decides what that means."""

import json
import tempfile
from pathlib import Path


def read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: object) -> bool:
    """Write via a temp file then rename. Returns whether it landed.

    A payload `json.dump` refuses is a failed write like any other: letting its `TypeError`/`ValueError` escape would both break the boolean contract and leave the staged file behind in `~/.ccw/`.
    """
    staged: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
            staged = Path(handle.name)  # Capture before dump so a half-failed dump can still be unlinked.
            json.dump(payload, handle, indent=1)
        staged.replace(path)
    except (OSError, TypeError, ValueError):
        if staged is not None:
            staged.unlink(missing_ok=True)
        return False
    return True
