#!/usr/bin/env python3
"""One question, rebuilt from a real-timeline run at step n, asked again with today's builder.

    python3 city_probe.py <run id> <n> [--set apart_words=1 ...] [--goal-text "..."] [--reps 2]

Prints Jev's top options and how much probability went to options whose house would touch another house,
stand apart, or stand one street from the civic centre (facts read from the option text).
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import city_demo as cd
import city_real as R


def key(c):
    return tuple(int(v.replace("p", "").replace("m", "-")) for v in re.findall(r"[pm]\d+", c))


def rebuild(run, n, goal):
    snaps = R.load_all(R.LOG)
    _, hs = R.petra_house_minutes(snaps)
    s = run["steps"][n - 1]
    houses = [key(x["choice"]) for x in run["steps"][:n - 1]]
    drop = {(d["tpl"], d["x"], d["z"]) for d in run.get("dropped", []) if d["minute"] <= s["minute"]}
    snap = json.loads(json.dumps(snaps[s["minute"]]))
    snap["structures"] = [x for x in snap["structures"] if not R.is_house(x) and R.skey(x) not in drop]
    b = (R.PiecesBuilder if R.VARIANT["pieces"] else R.Builder)(R.Map(snap, hs), goal, houses)
    return R.build(b, goal, houses, n, len(run["steps"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("n", type=int)
    ap.add_argument("--set", action="append", default=[], help="VARIANT flag, e.g. apart_words=1")
    ap.add_argument("--flags", default="", help="comma list of VARIANT flags to switch on, e.g. apart_words,compose")
    ap.add_argument("--goal-text", default=None)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args(argv)
    R.VARIANT.update(pieces=True)
    a.set += [f"{f}=1" for f in a.flags.split(",") if f]
    for kv in a.set:
        k, v = kv.split("=", 1)
        R.VARIANT[k] = v not in ("0", "false", "")
    run = json.loads((R.RUNS_DIR / f"{a.run}.json").read_text())
    goal = a.goal_text or run["goal_text"]
    prompt, options = rebuild(run, a.n, goal)
    byid = {o["id"]: o for o in options}
    for rep in range(a.reps):
        r = cd.ask(prompt, options)
        if not r.get("ok"):
            print("error", r.get("error"))
            return 1
        p = r["probabilities"]

        def mass(test):
            return sum(v for k, v in p.items() if k in byid and test(byid[k]["text"]))
        touch = mass(lambda t: "shoulder to shoulder with" in t and "house" in t.split("shoulder to shoulder with")[1].split(";")[0]
                     or "touches a house only at a corner" in t)
        apart = mass(lambda t: "stands apart" in t)
        street = mass(lambda t: "one street (6 m) from the civic centre;" in t[:110])
        ch = byid[r["choice"]]["text"]
        print(f"[{rep}] P(touch a house) {touch:.2f}  P(stands apart) {apart:.2f}  P(one street from CC) {street:.2f}  "
              f"choice: {ch[8:150]}")
        if rep == 0:
            for k in sorted(p, key=lambda k: -p[k])[:a.top]:
                print(f"     {p[k]:.2f} {byid[k]['text'][8:230]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
