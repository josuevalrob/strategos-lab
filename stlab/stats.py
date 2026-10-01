"""Per-minute game statistics from engine.log ("[strategos] stats {...}", head.js reportStats).

Read incrementally (offset cache per file), so a live game's growing log is cheap to poll.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

TAG = "[strategos] stats "
_cache: dict[str, dict] = {}
_lock = threading.Lock()


def read(path: Path | None) -> list[dict]:
    """Every stats line of ``path`` (first one per minute), oldest first."""
    if path is None or not path.exists():
        return []
    key = str(path)
    with _lock:
        c = _cache.get(key)
        size = path.stat().st_size
        if c is None or size < c["offset"]:
            c = _cache[key] = {"offset": 0, "rows": {}, "rest": b""}
        if size > c["offset"]:
            with path.open("rb") as f:
                f.seek(c["offset"])
                data = c["rest"] + f.read()
            c["offset"] = size
            lines = data.split(b"\n")
            c["rest"] = lines.pop()  # a line still being written
            for raw in lines:
                i = raw.find(TAG.encode())
                if i < 0:
                    continue
                try:
                    row = json.loads(raw[i + len(TAG):].decode("utf-8", "replace"))
                except ValueError:
                    continue
                c["rows"].setdefault(row.get("m"), row)
        return [c["rows"][m] for m in sorted(c["rows"], key=lambda m: (m is None, m))]
