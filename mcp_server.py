#!/usr/bin/env python3
"""Strategos Lab as an MCP server (stdio, stdlib only): read logged model questions, edit
prompts / Q&A files in memory, ask Jev + Laya, compare with what was logged.

It talks to the running lab (LAB_URL, default http://127.0.0.1:8765), so it reuses the lab's
warm Laya.  Read + ask only: nothing here writes files or commits; applying a change stays
with Josue in the lab's Edit tab.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

LAB = os.environ.get("LAB_URL", "http://127.0.0.1:8765").rstrip("/")
START = "cd ~/Projects/strategos-lab && python3 lab.py"


def _get(route: str, **q) -> dict:
    url = LAB + route + ("?" + urllib.parse.urlencode(q) if q else "")
    return _send(urllib.request.Request(url))


def _post(path: str, body: dict, timeout: float = 600) -> dict:
    return _send(urllib.request.Request(LAB + path, data=json.dumps(body).encode(), method="POST",
                                        headers={"X-Lab": "1", "Content-Type": "application/json"}),
                 timeout)


def _send(req, timeout: float = 120) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 -- localhost only
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except (ValueError, OSError):
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Strategos Lab is not running at {LAB} ({exc}). Start it: {START}") from None


def _questions(req) -> dict | None:
    if not isinstance(req, dict):
        return None
    return req.get("questions") or (req.get("response_format") or {}).get("questions")


# -- tools ----------------------------------------------------------------------------------
def list_runs(limit: int = 20) -> dict:
    d = _get("/api/runs")
    runs = sorted(d.get("runs") or [], key=lambda r: -r.get("mtime", 0))[:limit]
    return {"runs": [{"run": r["id"], "model": r.get("model") or r.get("adapter"), "questions": r.get("rows"),
                      "kinds": r.get("kinds"), "qa_version": r.get("qa_version"),
                      "status": "live" if r.get("live") else "finished" if r.get("finished") else "stopped",
                      "mtime": r.get("mtime")} for r in runs]}


def list_questions(run: str, kind: str = "play", limit: int = 300) -> dict:
    d = _get("/api/timeline", run=run)
    es = [e for e in d.get("entries") or [] if kind in ("all", e.get("kind"))][:limit]
    return {"run": d.get("run", {}).get("id") if isinstance(d.get("run"), dict) else run, "questions": [{
        "idx": e["idx"], "qid": e.get("qid"), "player": e.get("player"), "kind": e.get("kind"),
        "minute": e.get("minute"), "options": e.get("options"), "rule": e.get("rule"),
        "choice": e.get("choice"), "p": e.get("p"),
        "why_asked": "; ".join(x.get("text", "") for t in e.get("trigger") or [] for x in t.get("events") or []),
        "petra_did": ((e.get("game") or {}).get("applied") or {}).get("text")} for e in es]}


def get_question(run: str, idx: int) -> dict:
    d = _get("/api/question", run=run, idx=idx)
    if not d.get("ok", True) and d.get("error"):
        return d
    io, e = d.get("io") or {}, d.get("entry") or {}
    return {"idx": idx, "qid": e.get("qid"), "kind": e.get("kind"), "minute": e.get("minute"),
            "prompt": io.get("prompt"), "question": _questions(io.get("request")),
            "prompt_source": io.get("mode"), "notes": io.get("notes"),
            "logged": {"choice": e.get("choice"), "p": e.get("p"), "probabilities": e.get("probs"),
                       "rule": e.get("rule"), "model": e.get("adapter")},
            "petra_did": ((e.get("game") or {}).get("applied") or {}).get("text")}


def ask(run: str, idx: int, prompt: str | None = None, question: dict | None = None,
        models: list | None = None, goal: str | None = None) -> dict:
    models = [m for m in (models or ["jev", "laya"]) if m in ("jev", "laya", "pplx")]

    def one(m):
        body = {"run": run, "idx": idx, "model": m}
        if prompt:
            body["prompt"] = prompt
        if question:
            body["questions"] = question
        if goal:
            body["goal"] = goal
        r = _post("/api/ask", body).get("result") or {}
        return m, {k: r.get(k) for k in ("ok", "choice", "probabilities", "error", "ms", "raw")}

    with ThreadPoolExecutor(3) as ex:
        out = dict(ex.map(one, models))
    q = get_question(run, idx)
    out["logged"] = q.get("logged")
    out["edited"] = {"prompt": bool(prompt), "question": bool(question), "goal": goal}
    return out


def list_files() -> dict:
    d = _get("/api/files")
    paths: list[str] = []

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("path"), str):
                paths.append(x["path"])
            else:
                for v in x.values():
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    for key in ("civs", "heroes", "questions", "doctrine"):
        walk(d.get(key))
    return {"files": paths}


def get_file(path: str) -> dict:
    d = _get("/api/file", path=path)
    return {"path": path, "exists": d.get("exists"), "text": d.get("text"), "error": d.get("error")}


def try_files(run: str, idxs: list, edits: dict, models: list | None = None) -> dict:
    return _post("/api/try_files", {"run": run, "idxs": idxs, "edits": edits,
                                    "models": models or ["jev", "laya"]})


RES = ("food", "wood", "stone", "metal")


def _tot(o) -> float:
    if isinstance(o, dict):
        return float(o["total"]) if "total" in o else sum(float(v) for v in o.values() if isinstance(v, (int, float)))
    return float(o or 0)


def _player(p: dict) -> dict:
    s = p.get("stats") or {}
    g = s.get("resourcesGathered") or {}
    gathered = {r: round(g.get(r) or 0) for r in RES}
    eco = round((sum(gathered.values()) + (s.get("tradeIncome") or 0)) / 10)
    mil = round(((s.get("enemyUnitsKilledValue") or 0) + (s.get("unitsCapturedValue") or 0)
                 + (s.get("enemyBuildingsDestroyedValue") or 0) + (s.get("buildingsCapturedValue") or 0)) / 10)
    expl = round((s.get("percentMapExplored") or 0) * 10)
    return {"id": p.get("id"), "name": p.get("name"), "civ": p.get("civ"), "state": p.get("state"),
            "phase": p.get("phase"), "pop": p.get("pop"), "popLimit": p.get("popLimit"),
            "stock": {r: int((p.get("stock") or {}).get(r) or 0) for r in RES},
            "alive": p.get("alive"), "gathered": gathered,
            "units": {"trained": _tot(s.get("unitsTrained")), "lost": _tot(s.get("unitsLost")),
                      "killed": _tot(s.get("enemyUnitsKilled"))},
            "buildings": {"constructed": _tot(s.get("buildingsConstructed")), "lost": _tot(s.get("buildingsLost")),
                          "destroyed": _tot(s.get("enemyBuildingsDestroyed"))},
            "map_control_pct": s.get("percentMapControlled"),
            "score": {"economy": eco, "military": mil, "exploration": expl, "total": eco + mil + expl}}


def get_stats(run: str, minute: int | None = None, series: bool = False, raw: bool = False) -> dict:
    """Game statistics (engine.log "[strategos] stats" lines, one per game minute)."""
    rows = _get("/api/stats", run=run).get("minutes") or []
    if not rows:
        return {"run": run, "minutes": 0, "note": "no stats lines: only games started after 0AD commit "
                "1226e3a9a1 (2026-10-01 11:26) write them"}
    pick = rows[-1]
    if minute is not None:
        pick = rows[0]
        for r in rows:
            if (r.get("m") or 0) <= minute:
                pick = r
    out = {"run": run, "minutes_logged": len(rows), "last_minute": rows[-1].get("m"),
           "minute": pick.get("m"), "players": [_player(p) for p in pick.get("players") or []]}
    if raw:
        out["raw"] = pick
    if series:
        out["series"] = [{"m": r.get("m"), "players": [
            {"id": q["id"], "pop": q["pop"], "soldiers": (q["alive"] or {}).get("soldiers"),
             "gathered": sum(q["gathered"].values()), "killed": q["units"]["killed"], "lost": q["units"]["lost"],
             "score": q["score"]["total"]} for q in map(_player, r.get("players") or [])]} for r in rows]
    return out


S = {"type": "string"}
I = {"type": "integer"}
MODELS = {"type": "array", "items": {"type": "string", "enum": ["jev", "laya", "pplx"]},
          "description": "default both"}
TOOLS = {
    "list_runs": (list_runs, "Logged Strategos games (runs), newest first: run id, model, question count, status.",
                  {"limit": I}, []),
    "list_questions": (list_questions, "Questions asked in one run, in game order: idx (use it in the other "
                       "tools), qid, minute, options, rule's pick, model's choice + p, why asked, what Petra did. "
                       "kind defaults to 'play'; 'all' for every kind.", {"run": S, "kind": S, "limit": I}, ["run"]),
    "get_question": (get_question, "One question: the exact (or rebuilt) prompt, the question spec "
                     "(instructions + criteria per option), the logged answer and what Petra did.",
                     {"run": S, "idx": I}, ["run", "idx"]),
    "ask": (ask, "Ask Jev and/or Laya one logged question again, optionally with an edited prompt and/or "
            "question spec (same shape get_question returns), or with a parent's goal (v2: the prompt is rebuilt "
            "by the asker's pipe and the goal step adds it). Returns both answers next to the logged one. "
            "Nothing is saved.", {"run": S, "idx": I, "prompt": S, "question": {"type": "object"},
                                  "models": MODELS, "goal": S}, ["run", "idx"]),
    "list_files": (list_files, "The Q&A files the prompts are built from (civ, heroes, question specs, "
                   "doctrine), repo-relative paths.", {}, []),
    "get_file": (get_file, "Current text of one Q&A file.", {"path": S}, ["path"]),
    "get_stats": (get_stats, "Live/after-game statistics for every player, like 0 A.D.'s summary screen: score "
                  "(economy/military/exploration), pop, stock, gathered, units alive/trained/lost/killed, buildings, "
                  "map control. Latest minute by default, or 'minute'; series=true adds per-minute trends; "
                  "raw=true adds the full StatisticsTracker data.",
                  {"run": S, "minute": I, "series": {"type": "boolean"}, "raw": {"type": "boolean"}}, ["run"]),
    "try_files": (try_files, "Test edits to Q&A files without writing them: rebuild the prompts of the given "
                  "questions (idxs, max 50) from the edited files (edits = {path: full new text}), ask the "
                  "models, and report per question the logged choice vs the new ones ('changed').",
                  {"run": S, "idxs": {"type": "array", "items": I}, "edits": {"type": "object"},
                   "models": MODELS}, ["run", "idxs", "edits"]),
}


def _tool_list():
    return [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r}}
            for n, (_, d, p, r) in TOOLS.items()]


def handle(msg: dict):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "strategos-lab", "version": "1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": _tool_list()}
    if method == "tools/call":
        p = msg.get("params") or {}
        fn = TOOLS.get(p.get("name"), (None,))[0]
        if fn is None:
            return {"content": [{"type": "text", "text": f"unknown tool {p.get('name')}"}], "isError": True}
        try:
            out = fn(**(p.get("arguments") or {}))
            return {"content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)}],
                    "isError": isinstance(out, dict) and out.get("ok") is False}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
    if mid is None:
        return None  # notification
    raise LookupError(method)


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        try:
            res = handle(msg)
            if msg.get("id") is None:
                continue
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": res}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32601, "message": "method not found"}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
