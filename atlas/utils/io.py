from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any


def ensure_dir(path: Path) -> None:
    """Create directory (and parents) if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)


def download_file(url: str, dest: Path, *, overwrite: bool = False, timeout_s: int = 60) -> Path:
    """
    Download a URL to `dest`.

    - overwrite=False avoids re-downloading on reruns
    - timeout_s prevents hanging indefinitely
    """
    ensure_dir(dest.parent)

    if dest.exists() and not overwrite:
        return dest

    req = urllib.request.Request(url, headers={"User-Agent": "Atlas/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        dest.write_bytes(resp.read())

    return dest


def write_json(path: Path, obj: Any, *, indent: int = 2) -> None:
    """Write JSON to disk (UTF-8)."""
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> Any:
    """Read JSON from disk."""
    return json.loads(path.read_text(encoding="utf-8"))