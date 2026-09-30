"""L2 / L6: runs, their advisor logs and engine.log, joined into a timeline.

A run is any directory with ``advisor/*.jsonl`` under the results roots.  A
:class:`RunModel` reads its files incrementally (byte offsets, partial lines
kept), so the same object serves a finished game and a live one.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from . import code, config

ENGINE_RE = re.compile(r"PlayerID (\d+) \|\s+\[strategos\] (.*)$")
ASKED_RE = re.compile(r"^p(\d+) (\w+) at (\S*): asked q#(\d+)(?: options (\S+))? \(rule ([^)]*)\)")
SETTLED_RE = re.compile(r"^p(\d+) q#(\d+) (\w+)=(\S+) by (\S+)(?: p=(\S+))? \((.*)\)\s*$")
L3_APPLIED_RE = re.compile(r"^p(\d+) q#(\d+) applied (\S+) -> (\S+) via (.*?)\s*$")
QREF_RE = re.compile(r"q#(\d+)(?!\d)")
ROUTED_RE = re.compile(r"^q#\d+(?:/(\w+))? (\S+?)/(\S+) -> ([^:]+): (.*)$")


class EngineIndex:
    """Everything engine.log says about each (player, qid)."""

    def __init__(self, default_player: int | None = None):
        self.default_player = default_player
        self.lines = 0
        self.entries: dict[tuple, dict] = {}

    def _entry(self, key):
        e = self.entries.get(key)
        if e is None:
            e = self.entries[key] = {"asked": {}, "settled": None, "applied": [], "l3": None}
        return e

    def feed(self, raw: str) -> list[tuple]:
        """One log line; returns the keys it touched."""
        self.lines += 1
        n = self.lines
        m = ENGINE_RE.search(raw)
        if m:
            player, text = int(m.group(1)), m.group(2).rstrip()
        elif "[strategos]" in raw or "PlayerID" in raw:
            return []
        else:
            # head.log: the strategos lines without the engine's prefix.
            text, player = raw.rstrip("\n"), self.default_player
            if not text or text.startswith(("Turn ", "Main log", "Interesting log", "Replay written")):
                return []
        if "q#" not in text:
            return []
        a = ASKED_RE.match(text)
        if a:
            key = (int(a.group(1)), int(a.group(4)))
            self._entry(key)["asked"][a.group(2)] = {
                "line": n, "text": text, "events": a.group(3),
                "options": a.group(5).split("/") if a.group(5) else None, "rule": a.group(6)}
            return [key]
        s = SETTLED_RE.match(text)
        if s:
            key = (int(s.group(1)), int(s.group(2)))
            self._entry(key)["settled"] = {"line": n, "text": text, "kind": s.group(3),
                                           "choice": s.group(4), "by": s.group(5), "p": s.group(6),
                                           "note": s.group(7)}
            return [key]
        l3 = L3_APPLIED_RE.match(text)
        if l3:
            key = (int(l3.group(1)), int(l3.group(2)))
            order = None if l3.group(4) == "none" else l3.group(4)
            via = l3.group(5)
            self._entry(key)["l3"] = {"line": n, "text": text, "choice": l3.group(3), "order": order,
                                      "managers": [] if via == "none" else
                                      [x.strip() for x in via.split(",") if x.strip()]}
            return [key]
        if "asked q#" in text:
            return []   # a part's own id (one question per turn): not a desk qid
        touched = []
        for qm in QREF_RE.finditer(text):
            key = (player, int(qm.group(1)))
            if key in touched:
                continue
            self._entry(key)["applied"].append({"line": n, "text": text})
            touched.append(key)
        return touched

    def get(self, player, qid) -> dict | None:
        return self.entries.get((player, qid))


def _routed(entry: dict) -> dict | None:
    for a in entry["applied"]:
        m = ROUTED_RE.match(a["text"])
        if m and " ignored: " not in a["text"]:
            return {"part": m.group(1), "order": f"{m.group(2)}/{m.group(3)}",
                    "managers": [x.strip() for x in m.group(4).split(",")],
                    "text": f"{m.group(2)}/{m.group(3)} -> {m.group(4).strip()}: {m.group(5)}",
                    "line": a["line"]}
    return None


def _petra_detail(entry: dict) -> str | None:
    for a in entry["applied"]:
        pm = re.match(r"^q#\d+ (advance|train:workers|train:soldiers): (.*)$", a["text"])
        if pm:
            return pm.group(2)
    return None


def applied_summary(entry: dict | None, choice: str | None = None, kind: str | None = None) -> dict:
    """What engine.log says the game did with a settled question.  Never guesses:
    a run without a q#-tagged line for an answer says "unknown"."""
    if not entry:
        return {"state": "missing", "text": "no engine.log line for this q#", "order": None,
                "managers": []}
    settled = entry.get("settled")
    routed = _routed(entry)
    l3 = entry.get("l3")
    if l3:
        base = {"source": "l3", "line": l3["line"], "managers": l3["managers"]}
        if l3["order"] is None:
            if l3["choice"] == "economy":
                return {**base, "state": "none", "text": "economy: no order sent", "order": None}
            return {**base, "state": "none", "order": None,
                    "text": f"{l3['choice']} -> none: no new order (a repeat of the standing "
                            "order is not re-sent)"}
        order = l3["order"]
        if routed and routed["order"].endswith("/" + order):
            order = routed["order"]
        detail = routed["text"].split(": ", 1)[-1] if routed else _petra_detail(entry)
        return {**base, "state": "applied", "order": order,
                "text": f"{l3['choice']} -> {order} via {', '.join(l3['managers']) or 'none'}"
                        + (f": {detail}" if detail else "")}
    if settled and settled.get("by") == "rule":
        out = {"state": "rule", "order": routed["order"] if routed else None,
               "managers": routed["managers"] if routed else [],
               "text": f"rule's pick {settled['choice']} (the model gave no answer)"}
        if routed:
            out["text"] += f"; {routed['text']}"
            out["line"] = routed["line"]
        elif any("no order sent" in a["text"] for a in entry["applied"]):
            out["text"] += "; no order sent"
        return out
    if routed:
        return {"state": "applied", **{k: routed[k] for k in ("part", "order", "managers", "text", "line")}}
    for a in entry["applied"]:
        t = a["text"]
        if "no order sent" in t:
            return {"state": "none", "text": "no order sent (economy)", "order": None, "managers": [],
                    "line": a["line"]}
        pm = re.match(r"^q#\d+ (advance|train:workers|train:soldiers): (.*)$", t)
        if pm:
            return {"state": "applied", "text": f"{pm.group(1)}: {pm.group(2)}", "order": pm.group(1),
                    "managers": [], "line": a["line"]}
        if " ignored: " in t or " failed: " in t:
            return {"state": "ignored", "text": t, "order": None, "managers": [], "line": a["line"]}
        if t.startswith("stage decline"):
            return {"state": "applied", "text": t, "order": "walls/wall", "managers": [],
                    "line": a["line"]}
        if t.startswith("raid ") or t.startswith("guard: "):
            return {"state": "applied", "text": t, "order": None, "managers": [], "line": a["line"]}
    for a in entry["applied"]:
        return {"state": "other", "text": a["text"], "order": None, "managers": [], "line": a["line"]}
    if not settled:
        return {"state": "unsettled", "text": "asked, never settled in engine.log", "order": None,
                "managers": []}
    if kind == "opponent_class" or settled.get("kind") == "opponent_class":
        return {"state": "applied", "text": f"the head's opponent read is now {settled['choice']}",
                "order": None, "managers": []}
    return {"state": "unknown", "text": "unknown: no q#-tagged order line in engine.log",
            "order": None, "managers": []}


def humanize_events(kind: str, events: str | None, classes=()) -> list[dict]:
    """``"hero_next:slot pass_order:dying+phase"`` -> [{part, events: [{code, text}]}]."""
    if not events:
        return [{"part": kind, "events": [{"code": "", "text": "asked every decision tick"
                                           if kind == "opponent_class" else "no event recorded"}]}]
    out = []
    chunks = events.split(" ") if kind == "play" else [f"{kind}:{events}"]
    for chunk in chunks:
        part, _, evs = chunk.partition(":")
        if not evs:
            part, evs = kind, part
        words = code.PART_EVENT_WORDS.get(part, {})
        items = []
        for ev in [e for e in evs.split("+") if e]:
            if ev in words:
                text = words[ev]
            elif ev in code.EVENT_WORDS:
                text = code.EVENT_WORDS[ev]
            elif ev in classes or ev in code.KNOWN_OPTIONS["opponent_class"]:
                text = f"a new opponent read: {ev}"
            else:
                text = ev
            items.append({"code": ev, "text": text})
        out.append({"part": part, "events": items})
    return out


def _minute(row: dict) -> float | None:
    f = row.get("features") or {}
    m = f.get("game_minute")
    if isinstance(m, (int, float)):
        return round(float(m), 2)
    t = row.get("askedTime")
    return round(t / 60000.0, 2) if isinstance(t, (int, float)) else None


class RunModel:
    def __init__(self, rid: str, run_dir: Path):
        self.id = rid
        self.dir = Path(run_dir)
        self.lock = threading.RLock()
        self.offsets: dict[str, int] = {}
        self.partial: dict[str, str] = {}
        self.line_no: dict[str, int] = {}
        self.headers: list[dict] = []
        self.rows: list[dict] = []
        self.rows_by_key: dict[tuple, list[int]] = {}
        self.rev = 0
        self.engine = EngineIndex()
        self.engine_path: Path | None = None
        self.last_change = 0.0

    # -- files ---------------------------------------------------------------
    def jsonl_files(self) -> list[Path]:
        adir = self.dir / "advisor"
        return sorted(adir.glob("*.jsonl")) if adir.exists() else []

    def _engine_file(self) -> Path | None:
        for name in ("engine.log", "head.log"):
            p = self.dir / name
            if p.exists():
                return p
        return None

    def _read_new(self, path: Path) -> list[str]:
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            return []
        off = self.offsets.get(key, 0)
        if size == off:
            return []
        with open(path, "rb") as fh:
            fh.seek(off)
            data = fh.read(size - off)
        self.offsets[key] = size
        text = self.partial.get(key, "") + data.decode("utf-8", "replace")
        parts = text.split("\n")
        self.partial[key] = parts.pop()      # an unfinished last line waits
        return parts

    def _reset(self) -> None:
        self.offsets, self.partial, self.line_no = {}, {}, {}
        self.headers, self.rows, self.rows_by_key = [], [], {}
        self.engine = EngineIndex()
        self.engine_path = None

    def _shrunk(self) -> bool:
        for key, off in self.offsets.items():
            try:
                if Path(key).stat().st_size < off:
                    return True
            except OSError:
                return True
        return False

    def refresh(self) -> bool:
        """Read what was appended since the last call.  True when anything changed."""
        with self.lock:
            changed = False
            if self._shrunk():
                # A file was rewritten (a new game in the same folder): read it all again.
                self._reset()
                self.rev += 1
                changed = True
            for path in self.jsonl_files():
                key = str(path)
                header = None
                for line in self._read_new(path):
                    self.line_no[key] = self.line_no.get(key, 0) + 1
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("header"):
                        header = dict(obj, _file=path.name)
                        self.headers.append(header)
                        if self.engine.default_player is None:
                            self.engine.default_player = _strategos_player(header)
                        continue
                    self.rev += 1
                    obj["_idx"] = len(self.rows)
                    obj["_file"] = path.name
                    obj["_line"] = self.line_no[key]
                    obj["_rev"] = self.rev
                    obj["_header"] = self._header_index(path.name)
                    self.rows.append(obj)
                    k = (obj.get("player"), obj.get("qid"))
                    self.rows_by_key.setdefault(k, []).append(obj["_idx"])
                    changed = True
            ep = self._engine_file()
            if ep is not None:
                if self.engine_path is not None and ep != self.engine_path:
                    self.engine = EngineIndex(self.engine.default_player)
                    self.offsets.pop(str(self.engine_path), None)
                self.engine_path = ep
                for line in self._read_new(ep):
                    for key in self.engine.feed(line):
                        for idx in self.rows_by_key.get(key, []):
                            self.rev += 1
                            self.rows[idx]["_rev"] = self.rev
                        changed = True
            if changed:
                self.last_change = time.time()
            return changed

    def _header_index(self, fname: str) -> int | None:
        for i in range(len(self.headers) - 1, -1, -1):
            if self.headers[i].get("_file") == fname:
                return i
        return None

    def header_of(self, row: dict) -> dict:
        i = row.get("_header")
        if i is not None and 0 <= i < len(self.headers):
            return self.headers[i]
        return self.headers[0] if self.headers else {}

    # -- views ---------------------------------------------------------------
    def mtime(self) -> float:
        ts = [0.0]
        for p in self.jsonl_files() + ([self._engine_file()] if self._engine_file() else []):
            try:
                ts.append(p.stat().st_mtime)
            except OSError:
                pass
        return max(ts)

    def finished(self) -> bool:
        return (self.dir / "summary.json").exists()

    def is_live(self, window_s: float) -> bool:
        return not self.finished() and (time.time() - self.mtime()) < window_s

    def kinds(self) -> dict:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r.get("kind")] = out.get(r.get("kind"), 0) + 1
        return out

    def info(self, window_s: float = 120.0) -> dict:
        h = self.headers[0] if self.headers else {}
        ad = h.get("adapter") or {}
        qa_block = h.get("qa") or None
        return {
            "id": self.id, "dir": str(self.dir), "rows": len(self.rows), "kinds": self.kinds(),
            "adapter": ad.get("name"), "version": ad.get("version") or ad.get("model"),
            "model": ad.get("model"), "prompt_variant": ad.get("prompt_variant"),
            "mod_sha": h.get("mod_sha"), "qa": qa_block,
            "qa_version": qa_version(h),
            "players": (h.get("engine") or {}).get("aiPlayers"),
            "mtime": self.mtime(), "finished": self.finished(), "live": self.is_live(window_s),
            "engine_log": self.engine_path.name if self.engine_path else None,
            "headers": len(self.headers),
        }

    def entry(self, row: dict, classes=()) -> dict:
        eng = self.engine.get(row.get("player"), row.get("qid"))
        kind = row.get("kind")
        feats = row.get("features") or {}
        meta = row.get("meta") or {}
        asked = (eng or {}).get("asked", {}).get(kind) if eng else None
        settled = eng.get("settled") if eng else None
        game_choice = settled["choice"] if settled else None
        return {
            "idx": row["_idx"], "rev": row["_rev"], "serial": row.get("serial"), "qid": row.get("qid"),
            "player": row.get("player"), "kind": kind, "minute": _minute(row),
            "askedTime": row.get("askedTime"), "events": feats.get("events"),
            "trigger": humanize_events(kind, feats.get("events"), classes),
            "options": row.get("options") or [], "rule": row.get("rule"), "choice": row.get("choice"),
            "p": row.get("p"), "probs": meta.get("probabilities"), "outcome": row.get("outcome"),
            "adapter": row.get("adapter"), "latency_ms": row.get("latency_ms"),
            "error": row.get("error"), "has_io": bool(row.get("io")),
            "source": {"file": row.get("_file"), "line": row.get("_line")},
            "game": {
                "asked": asked, "settled": settled,
                "choice": game_choice, "by": settled["by"] if settled else None,
                "applied": applied_summary(eng, game_choice, kind),
                "lines": (eng or {}).get("applied", [])[:12],
            },
        }

    def timeline(self, kind: str | None = None, since_rev: int = 0, classes=()) -> list[dict]:
        with self.lock:
            rows = [r for r in self.rows if (kind in (None, "", "all") or r.get("kind") == kind)
                    and r["_rev"] > since_rev]
            rows.sort(key=lambda r: (r.get("askedTime") or 0, r.get("qid") or 0, r["_idx"]))
            return [self.entry(r, classes) for r in rows]

    def row(self, idx: int) -> dict:
        with self.lock:
            return self.rows[idx]


def _strategos_player(header: dict) -> int | None:
    players = ((header.get("engine") or {}).get("aiPlayers")) or []
    ours = [p.get("id") for p in players if str(p.get("ai", "")).startswith("strategos")]
    return ours[0] if len(ours) == 1 else None


def qa_version(header: dict) -> str:
    qa_block = header.get("qa") or {}
    if qa_block.get("commit"):
        v = str(qa_block["commit"])[:10]
        return v + ("-dirty" if qa_block.get("dirty") else "")
    return str(header.get("mod_sha") or "unknown")


def civ_of(header: dict, player) -> str | None:
    for p in ((header.get("engine") or {}).get("aiPlayers")) or []:
        if p.get("id") == player:
            return p.get("civ")
    return None


class Registry:
    """All runs under the results roots; RunModels kept and refreshed incrementally."""

    SKIP = {"advisor", "qa_snapshot", "replay1", "replay2", "__pycache__"}

    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.models: dict[str, RunModel] = {}
        self.lock = threading.Lock()
        self._scan_at = 0.0
        self._dirs: dict[str, Path] = {}

    def scan(self, force: bool = False) -> dict[str, Path]:
        if not force and time.time() - self._scan_at < 2.0:
            return self._dirs
        found = {}
        for root in self.cfg.run_roots():
            if not root.exists():
                continue
            base = root.parent
            for dirpath, dirnames, _files in os.walk(root):
                d = Path(dirpath)
                if (d / "advisor").is_dir() and any((d / "advisor").glob("*.jsonl")):
                    found[str(d.relative_to(base))] = d
                dirnames[:] = [n for n in dirnames if n not in self.SKIP and not n.startswith(".")]
        self._dirs = found
        self._scan_at = time.time()
        return found

    def get(self, rid: str) -> RunModel:
        dirs = self.scan()
        if rid not in dirs:
            dirs = self.scan(force=True)
        if rid not in dirs:
            raise KeyError(f"no run {rid!r}")
        with self.lock:
            m = self.models.get(rid)
            if m is None:
                m = self.models[rid] = RunModel(rid, dirs[rid])
        m.refresh()
        return m

    def all(self) -> list[RunModel]:
        return [self.get(rid) for rid in sorted(self.scan())]

    def newest(self) -> RunModel | None:
        dirs = self.scan(force=True)
        best, best_t = None, -1.0
        for rid, d in dirs.items():
            t = 0.0
            for p in list((d / "advisor").glob("*.jsonl")) + [d / "engine.log"]:
                try:
                    t = max(t, p.stat().st_mtime)
                except OSError:
                    pass
            if t > best_t:
                best, best_t = rid, t
        return self.get(best) if best else None

    def live_runs(self) -> list[str]:
        out = []
        for rid, d in self.scan().items():
            if (d / "summary.json").exists():
                continue
            t = 0.0
            for p in list((d / "advisor").glob("*.jsonl")) + [d / "engine.log"]:
                try:
                    t = max(t, p.stat().st_mtime)
                except OSError:
                    pass
            if time.time() - t < self.cfg.live_window_s:
                out.append(rid)
        return out
