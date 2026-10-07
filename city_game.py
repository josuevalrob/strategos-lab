#!/usr/bin/env python3
"""A live game's town (the strategos City Planner, 0AD strategos/v2) judged like a lab run, next to v1's town on
that game's map and timeline.

    python3 city_game.py <game run dir> [--minute 20]

Reads <run>/engine.log (snapshots, queue lines) and <run>/advisor/jev-p1.jsonl (the City Planner's answers).
Jev's town = every City Planner building (city/planner_classes.json classes) founded or standing in the snapshot
of that minute; Petra's other buildings stay where she put them.  For each house_place answer: the spot asked (the
request's position) and the building carrying that slot (snapshot "slot"): the distance between them.
Writes static/city/runs/<id>.json (mode real, town, game: shown by city.html) and its judge file
(city_judge.judge_town with the game's engine.log as the timeline, so v1 is built on the same map and queue events).
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import statistics
import sys
from pathlib import Path

import city_judge as J
import city_real as R

HS = {"w": R.PITCH, "d": R.PITCH, "tpl": "house"}


def rows(path: Path) -> list:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def value(v):
    """Advisor rows hold some fields as Python reprs."""
    if isinstance(v, str):
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return v
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir")
    ap.add_argument("--minute", type=int, default=20)
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir).expanduser().resolve()
    log = run_dir / "engine.log"
    snaps = R.load_all(log)
    minute = min(a.minute, max(snaps))
    snap = snaps[minute]
    R.TOWN_LOG = log                           # the judge and v1 read this game's snapshots and queue lines
    mine = [s for s in snap["structures"] if R.planner_struct(s)]
    rest = dict(snap, structures=[s for s in snap["structures"] if not R.planner_struct(s)])
    m = R.Map(rest, HS)
    words = R.PLANNER["words"]

    def kind_of(s):
        return words[next(c for c in R.PLANNER["classes"] if c in s["cls"])]

    adv = rows(run_dir / "advisor" / "jev-p1.jsonl")
    answers = [r for r in adv if r.get("kind") == "house_place" and r.get("outcome") == "sent"]
    gates = [r for r in adv if str(r.get("kind", "")).startswith("gate_") and r.get("choice")]
    by_slot = {tuple(s["slot"]): s for s in mine if s.get("slot")}
    steps, used, dists = [], set(), []
    for n, r in enumerate(answers, 1):
        req = value(r["request"])
        pos = tuple(req["position"])
        s = by_slot.get(pos)
        crit = value(r.get("io", {}).get("request", {}))
        probs = value(r.get("meta") or {}).get("probabilities", {})
        texts = (crit.get("questions", {}).get("house_place", {}).get("criteria", {}) if isinstance(crit, dict) else {})
        top = sorted(probs, key=lambda i: -probs[i])[:5]
        step = {"n": n, "minute": value(r.get("features") or {}).get("game_minute"), "prompt": r.get("io", {}).get("prompt"),
                "choice": r["choice"], "text": texts.get(r["choice"], ""), "p": float(r.get("p") or 0), "spot": list(pos),
                "top": [{"id": i, "p": probs[i], "text": texts.get(i, "")} for i in top]}
        if s is not None:
            used.add(s["id"])
            d = math.hypot(s["x"] - pos[0], s["z"] - pos[1])
            dists.append(d)
            step.update(kind=kind_of(s), rect=list(m.rect(s)), slot_dist=round(d, 1),
                        building=R.rect_world(m, m.rect(s), kind_of(s)), done=s["done"])
        else:
            step.update(kind=None, slot_dist=None, note="no building at this spot by minute %d" % minute)
        steps.append(step)
    for s in mine:                             # City Planner buildings Petra placed herself (released plans)
        if s["id"] not in used:
            steps.append({"n": len(steps) + 1, "minute": minute, "kind": kind_of(s), "rect": list(m.rect(s)),
                          "building": R.rect_world(m, m.rect(s), kind_of(s)), "text": "placed by Petra (released)",
                          "p": None, "slot_dist": None, "done": s["done"], "top": []})
    goal = next((ln[6:] for r in answers for ln in (r.get("io", {}).get("prompt") or "").splitlines()
                 if ln.startswith("Goal: ")), R.civ_line("spart"))
    events = R.queue_events(log, minute)
    rid = f"{run_dir.name}-game-spart"
    built = [x for x in steps if x.get("rect")]
    run = {"id": rid, "mode": "real", "town": True, "timeline": True, "game": True, "variant": "game", "civ": "spart",
           "dry": False, "goal_text": goal, "started": run_dir.name,
           "source": {"log": str(log), "until": minute, "minutes": [e["minute"] for e in events],
                      "events": [{k: e[k] for k in ("minute", "t", "tpl", "kind")} for e in events]},
           "rule": "in-game town: City Planner buildings at minute %d (live game %s)" % (minute, run_dir.name),
           "maps": {str(minute): R.map_payload(m)}, "map": R.map_payload(m), "dropped": [],
           "steps": [dict(x, minute=minute) if x.get("minute") is None else x for x in steps if x.get("rect")],
           "summary": {"buildings": len(built), "houses": len(built), "answers": len(answers),
                       "at_spot_median_m": round(statistics.median(dists), 1) if dists else None},
           "gates": [{"after": None, "ring": None, "side": g["kind"].replace("gate_", ""), "t": None, "rect": None,
                      "p": float(g.get("p") or 0),
                      "text": value(g.get("io", {}).get("request", {})).get("questions", {}).get(g["kind"], {})
                                  .get("criteria", {}).get(g["choice"], g["choice"])} for g in gates],
           "flags": {}}
    run["steps"].sort(key=lambda x: (x["minute"] or 0, x["n"]))
    R.save_real(run)
    j = J.judge_town(run)
    print(J.town_line(rid, j))
    print(f"answers {len(answers)}, City Planner buildings at minute {minute}: {len(mine)} "
          f"({len(used)} at an answered spot, {len(mine) - len(used)} placed by Petra); distance spot -> building: "
          f"median {run['summary']['at_spot_median_m']} m, max {round(max(dists), 1) if dists else None} m")
    print(f"gates: " + "; ".join(f"{g['side']}: {g['text'][:60]} ({g['p']:.2f})" for g in run["gates"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
