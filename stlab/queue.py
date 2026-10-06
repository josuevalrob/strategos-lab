"""The Queue view: every plan that entered Petra's queues in one run -- when, by whom
(Petra, or our persona -> leaf: option), what became of it, and what the King did.

Read from the run's own files, re-read whenever they grow (live while a game runs):
  engine.log      "[strategos] queue {...}" snapshots of the watched queues (the strategos
                  AI, queues.js) and the "[strategos] pN q#M applied/started/..." acks
  advisor/*.jsonl our requests (outcome "sent") and the queue_order rows (asked by the King
                  in older runs, by the Master of Coin since 2026-10-06; event_chain.sent =
                  our q#s that triggered the ask)
  pipeline.jsonl  queue_order triggers with no row: only "keep" was possible, so no ask
                  (older runs: rec "king"; newer: rec "role" == "master_of_coin" + "sent")

A plan is one Petra plan object: the snapshots give each plan's "waiting_s" since the AI
first saw it, so queue + what + (t - waiting_s) names the same plan across snapshots.
K = an applied King "front"/"fund" named this plan (the first plan of that kind in its
queue at that moment, as requests.js picks it).
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from . import runs

QUEUE_TAG = "[strategos] queue "
ACK_RE = re.compile(r"\[strategos\] p(\d+) q#(\d+) (applied|started|finished|trained|dropped|rejected)\b")
QUEUES_JSON = "binaries/data/mods/strategos/simulation/data/strategos/queues.json"
SAME_PLAN_S = 1.5   # waiting_s is rounded to seconds: one plan's first-seen time may move by 1 s

_cache: dict[str, tuple] = {}
_lock = threading.Lock()


def titles(repo: Path) -> dict[str, str]:
    """queue name -> its title in 0AD's queues.json (the one list the game and asker read)."""
    try:
        return json.loads((Path(repo) / QUEUES_JSON).read_text()).get("queues") or {}
    except (OSError, ValueError):
        return {}


def _player(m) -> int:
    h = m.headers[0] if m.headers else {}
    return runs._strategos_player(h) or 1


def _size(p: Path | None) -> int:
    try:
        return p.stat().st_size if p else -1
    except OSError:
        return -1


def _pipeline(run_dir: Path) -> list[dict]:
    p = Path(run_dir) / "pipeline.jsonl"
    out = []
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass   # a line being written right now
    return out


def table(repo: Path, m) -> dict:
    """The Queue view of run model ``m`` (cached until its files grow)."""
    engine = m.engine_path
    key = (_size(engine), _size(Path(m.dir) / "pipeline.jsonl"), len(m.rows))
    with _lock:
        hit = _cache.get(m.id)
        if hit and hit[0] == key:
            return hit[1]
    out = _build(repo, m, engine)
    with _lock:
        _cache[m.id] = (key, out)
    return out


def _build(repo: Path, m, engine: Path | None) -> dict:
    player = _player(m)
    names = titles(repo)
    with m.lock:
        rows = [r for r in m.rows if r.get("player") in (None, player) and r.get("qid") is not None]

    # Our requests and the King's asks, from the advisor rows.
    ours: dict[int, dict] = {}
    king_of: dict[int, dict] = {}          # our q# -> the King ask it triggered
    king_sends: dict[int, dict] = {}       # King q# -> his request (front / fund)
    for r in rows:
        chain = r.get("event_chain") or {}
        if r.get("kind") == "queue_order":    # the King's leaf in older runs, the Master of Coin's now
            ask = {"qid": r["qid"], "idx": r["_idx"], "choice": r.get("choice"), "outcome": r.get("outcome")}
            for q in chain.get("sent") or []:
                king_of[q] = ask
            if r.get("outcome") == "sent" and r.get("request"):
                king_sends[r["qid"]] = dict(r["request"], qid=r["qid"], idx=r["_idx"])
        elif r.get("outcome") == "sent":
            ours[r["qid"]] = {"qid": r["qid"], "idx": r["_idx"], "persona": chain.get("persona"),
                              "leaf": r.get("kind"), "option": r.get("choice")}
    king_asks = len({a["qid"] for a in king_of.values()})
    for rec in _pipeline(m.dir):            # King triggers he was not asked for (only "keep" possible)
        sent = rec["king"] if "king" in rec else rec.get("sent") if rec.get("role") == "master_of_coin" else None
        if sent is not None and not rec.get("leaves"):
            king_asks += 1
            for q in sent:
                king_of.setdefault(q, {"qid": None, "idx": None, "choice": None, "not_asked": True})

    plans: list[dict] = []
    by_kind: dict[tuple, list[dict]] = {}   # (queue, what) -> plans
    current: list[dict] = []                # the newest snapshot's plans, in queue order
    steps: dict[int, list[str]] = {}
    last_t = 0.0
    lines = engine.read_text(errors="replace").splitlines() if engine and engine.exists() else []
    for line in lines:
        i = line.find(QUEUE_TAG)
        if i >= 0:
            try:
                snap = json.loads(line[i + len(QUEUE_TAG):])
            except ValueError:
                continue
            if snap.get("player") != player:
                continue
            t = snap["t"] / 1000
            last_t = t
            now, seen = [], set()
            for qname, q in (snap.get("queues") or {}).items():
                for p in q.get("plans") or []:
                    born = t - (p.get("waiting_s") or 0)
                    same = by_kind.setdefault((qname, p["what"]), [])
                    plan = next((x for x in same if x["out"] is None and abs(x["in"] - born) <= SAME_PLAN_S
                                 and id(x) not in seen), None)
                    if plan is None:
                        plan = {"in": born, "queue": qname, "title": names.get(qname, qname), "what": p["what"],
                                "item": p.get("name") or p["what"], "n": 1, "ours": None, "out": None, "k": None}
                        same.append(plan)
                        plans.append(plan)
                    plan["n"] = max(plan["n"], p.get("n") or 1)
                    if p.get("ours") is not None:
                        plan["ours"] = p["ours"]
                    now.append(plan)
                    seen.add(id(plan))
            for plan in current:
                if id(plan) not in seen:
                    plan["out"] = t
            current = now
            continue
        a = ACK_RE.search(line)
        if a and int(a.group(1)) == player:
            qid, step = int(a.group(2)), a.group(3)
            steps.setdefault(qid, []).append(step)
            req = king_sends.get(qid)
            if req and step == "applied":
                target = next((p for p in current if p["queue"] == req.get("queue") and p["what"] == req.get("plan")), None)
                if target is not None:
                    target["k"] = {"qid": qid, "idx": req["idx"], "action": req.get("action")}

    out_rows = []
    for p in sorted(plans, key=lambda x: x["in"]):
        q = p["ours"]
        last = (steps.get(q) or [None])[-1] if q is not None else None
        if p["out"] is None:
            status = "waiting"
        elif q is not None:
            status = {"applied": "left", None: "left"}.get(last, last)
        else:
            status = "left"   # Petra's: the snapshot does not say whether it started or was dropped
        out_rows.append({
            "in": round(p["in"], 1), "queue": p["queue"], "title": p["title"], "item": p["item"], "n": p["n"],
            "by": ours.get(q) or ({"qid": q} if q is not None else None),
            "king": king_of.get(q) if q is not None else None,
            "k": p["k"], "out": round(p["out"], 1) if p["out"] is not None else None, "status": status,
            "steps": steps.get(q) if q is not None else None,
        })
    answers: dict[str, int] = {}
    for a in {a["qid"]: a for a in king_of.values() if a.get("qid")}.values():
        c = (a.get("choice") or "none").split(":")[0]
        answers[c] = answers.get(c, 0) + 1
    return {
        "run": m.id, "player": player, "end": round(last_t, 1), "queues": names,
        "summary": {"plans": len(out_rows), "ours": sum(1 for r in out_rows if r["by"]),
                    "sent": len(ours), "king_asks": king_asks, "king_answers": answers,
                    "king_moves": sum(1 for r in out_rows if r["k"]),
                    "not_seen": sorted(set(ours) - {r["by"]["qid"] for r in out_rows if r["by"]})},
        "rows": out_rows,
    }
