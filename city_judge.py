#!/usr/bin/env python3
"""Judge a real-map city run against the facts its civ line asks for -- evaluation only, never read by the builder.

    python3 city_judge.py <run id> [...]        # or: python3 city_judge.py --latest 9
    A town run (city_real.py --town) is judged on v1's measures (checks.json "town") next to v1's own town
    (city_v1.py, same timeline); both land in static/city/runs/<id>.judge.json for the page.

The checks per civ are data (city_data/checks.json); this code only computes generic facts:
- Walls = the civic centre + every ring piece (Jev's houses and Petra's structures) grown by TOL/2, so
  touching pieces leave no slit.  1 m cells, |u|, |v| <= HALF around the civic centre, in its own frame.
- Way out: the widest path from the civic centre's edge to the area's border.  Its narrowest point is a gate
  (width = 2 x clearance there).  Wall it off and look again, up to MAX_GATES times; if no way out is left the
  ring is closed and the gates found so far are its gates.
- Sides by angle in the CC frame: right flank 0, front 90, left flank 180, back 270; "corner" = within 15 deg
  of a diagonal.
"""
from __future__ import annotations

import glob
import heapq
import json
import math
import os
import re
import sys
from collections import deque
from pathlib import Path

import city_real as R

CHECKS = json.loads((Path(__file__).resolve().parent / "city_data" / "checks.json").read_text())
HALF = 90
MAX_GATES = 8
SECTORS = {"right flank": 0, "front": 90, "left flank": 180, "back": 270}
SLIT_M = 6          # an opening narrower than this is a slit, not a gate (a street is ~10 m); reported apart


def key(c):
    return tuple(int(v.replace("p", "").replace("m", "-")) for v in re.findall(r"[pm]\d+", c))


def final_map(run):
    snaps = R.load_all(R.LOG)
    _, hs = R.petra_house_minutes(snaps)
    last = run["steps"][-1]["minute"] if run.get("timeline") else run["source"]["minute"]
    drop = {(d["tpl"], d["x"], d["z"]) for d in run.get("dropped", [])}
    snap = json.loads(json.dumps(snaps[last]))
    snap["structures"] = [s for s in snap["structures"] if not R.is_house(s) and R.skey(s) not in drop]
    houses = [key(s["choice"]) for s in run["steps"] if s.get("choice")]
    return R.PiecesBuilder(R.Map(snap, hs), run["goal_text"], houses), houses


def near_when_built(run, i) -> bool:
    """House i next to a field (<= 6 m) or the woods (<= 10 m) on the map of the minute it was built
    (trees get cut and fields come later, so the last map would judge a different town)."""
    snaps = R.load_all(R.LOG)
    _, hs = R.petra_house_minutes(snaps)
    st = run["steps"][i]
    mnt = st.get("minute", run.get("source", {}).get("minute"))
    drop = {(d["tpl"], d["x"], d["z"]) for d in run.get("dropped", []) if d["minute"] <= mnt}
    snap = json.loads(json.dumps(snaps[mnt]))
    snap["structures"] = [s for s in snap["structures"] if not R.is_house(s) and R.skey(s) not in drop]
    m = R.Map(snap, hs)
    k = key(st["choice"])
    r = (k[0] * R.PITCH, k[1] * R.PITCH, R.PITCH / 2, R.PITCH / 2)
    f = min((R.Map.gap(r, m.rect(s)) for s in m.structs if s["tpl"].endswith("/field")), default=99)
    w = min((m.point_gap(r, x, z) for x, z in m.trees), default=99)
    return round(f) <= 6 or round(w) <= 10


def angdiff(a, b):
    return abs((a - b + 180) % 360 - 180)


def flood_gates(b, ps) -> dict:
    return flood(b.cc_rect, ps, R.TOL / 2, HALF, b.m.house_w)


def flood(cc, ps, grow, HALF, house_w=14, centre=False) -> dict:
    """Ways out from the civic centre between the pieces `ps` (each grown by `grow` m), on 1 m cells |u|, |v| <= HALF;
    each way out's narrowest point is a gate, walled off before the next.  centre=True paints a cell when its centre
    is inside the grown piece (gaps up to 2 x grow closed, widths true to the metre); else whole cells it touches."""
    n = 2 * HALF + 1
    wall = bytearray(n * n)

    def paint(buf, r, g):
        if centre:
            u0, u1 = int(math.ceil(r[0] - r[2] - g - 0.5)) + HALF, int(math.floor(r[0] + r[2] + g - 0.5)) + HALF
            v0, v1 = int(math.ceil(r[1] - r[3] - g - 0.5)) + HALF, int(math.floor(r[1] + r[3] + g - 0.5)) + HALF
        else:
            u0, u1 = int(math.floor(r[0] - r[2] - g)) + HALF, int(math.ceil(r[0] + r[2] + g)) + HALF
            v0, v1 = int(math.floor(r[1] - r[3] - g)) + HALF, int(math.ceil(r[1] + r[3] + g)) + HALF
        for i in range(max(0, u0), min(n, u1 + 1)):
            buf[i * n + max(0, v0): i * n + min(n, v1 + 1)] = b"\x01" * (min(n, v1 + 1) - max(0, v0))
    for p in ps:
        paint(wall, p["rect"], grow)
    # clearance (4-neighbour steps to the nearest ring piece; the civic centre itself does not narrow a way out)
    clear = [0 if wall[c] else 10 ** 6 for c in range(n * n)]
    dq = deque(c for c in range(n * n) if wall[c])
    while dq:
        c = dq.popleft()
        i, j = divmod(c, n)
        for x in (c + n if i + 1 < n else -1, c - n if i else -1, c + 1 if j + 1 < n else -1, c - 1 if j else -1):
            if x >= 0 and clear[x] > clear[c] + 1:
                clear[x] = clear[c] + 1
                dq.append(x)
    paint(wall, cc, 0.0)
    h = int(cc[2]) + 2
    starts = set()
    for t in range(-h, h + 1):
        for a, d in ((t, -h), (t, h), (-h, t), (h, t)):
            starts.add((a + HALF) * n + (d + HALF))
    gates = []
    for _ in range(MAX_GATES):
        best, prev, heap = {}, {}, []
        for c in starts:
            if not wall[c]:
                best[c], prev[c] = clear[c], -1
                heapq.heappush(heap, (-clear[c], c))
        out = None
        while heap:
            nb, c = heapq.heappop(heap)
            if -nb < best.get(c, -1):
                continue
            i, j = divmod(c, n)
            if i in (0, n - 1) or j in (0, n - 1):
                out = c
                break
            for x in (c + n if i + 1 < n else -1, c - n if i else -1, c + 1 if j + 1 < n else -1, c - 1 if j else -1):
                if x >= 0 and not wall[x]:
                    bx = min(-nb, clear[x])
                    if bx > best.get(x, -1):
                        best[x], prev[x] = bx, c
                        heapq.heappush(heap, (-bx, x))
        if out is None:
            reach = max((max(abs(divmod(c, n)[0] - HALF), abs(divmod(c, n)[1] - HALF)) for c in best), default=0)
            return {"closed": True, "gates": gates, "inside_reach": round(reach - cc[2]), "inside_m2": len(best)}
        bott = best[out]
        path, c = [], out
        while c != -1:
            path.append(c)
            c = prev[c]
        g = next(c for c in reversed(path) if clear[c] == bott)      # first narrowest cell from the CC side
        gi, gj = divmod(g, n)
        ang = math.degrees(math.atan2(gj - HALF, gi - HALF)) % 360
        cell = (gi - HALF, gj - HALF, 0.5, 0.5)
        two = sorted(ps, key=lambda p: R.Map.gap(cell, p["rect"]))[:2]
        width = 2 * bott - 1 + (round(2 * grow) if not centre else 1)
        gates.append({"at": round(ang), "width": width, "between": [p["kind"] for p in two],
                      "petra_slit": all(p["kind"] != "house" for p in two) and width < house_w,
                      "slit": width < SLIT_M,
                      "side": min(SECTORS, key=lambda s: angdiff(ang, SECTORS[s])),
                      "corner": any(angdiff(ang, k) <= 15 for k in (45, 135, 225, 315)),
                      "out_m": round(max(abs(gi - HALF), abs(gj - HALF)) - cc[2])})
        rad = bott + 1
        for i in range(max(0, gi - rad), min(n, gi + rad + 1)):
            for j in range(max(0, gj - rad), min(n, gj + rad + 1)):
                wall[i * n + j] = 1
    return {"closed": False, "gates": gates, "inside_reach": None, "inside_m2": None}


def judge(run) -> dict:
    b, houses = final_map(run)
    ps = b.pieces(houses)
    fl = flood_gates(b, ps)
    hs = set(houses)
    nb8 = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
    touching = sum(any((k[0] + a, k[1] + c) in hs for a, c in nb8) for k in houses)
    side_by = sum(any((k[0] + a, k[1] + c) in hs for a, c in nb8[:4]) for k in houses)
    near = sum(near_when_built(run, i) for i in range(len(houses)))
    n_h = max(1, len(houses))
    gates = [g for g in fl["gates"] if not g["slit"]]
    walls = flood_gates(b, [p for p in ps if p["kind"] != "field"])      # fields do not block units in the game
    facts = {"petra_slits": [g for g in fl["gates"] if g["slit"]], "inside_m2": fl["inside_m2"],
             "closed_without_fields": walls["closed"] and not [g for g in walls["gates"] if not g["slit"]],
             "closed": fl["closed"], "gates": gates, "inside_reach": fl["inside_reach"], "houses": len(houses),
             "touching": touching, "side_by_side": side_by, "next_to_field_or_wood": near}
    civ = run["civ"].split()[0]
    want = CHECKS.get(civ, {})
    fails = []
    if want.get("ring_closed") and not fl["closed"]:
        fails.append(f"ring open ({len(gates)}+ ways out)")
    if "gates_only_at" in want:
        bad = [g for g in gates if g["side"] not in want["gates_only_at"] or g["corner"]]
        if bad:
            fails.append("gate at " + ", ".join(f"{g['side']}{' corner' if g['corner'] else ''}" for g in bad))
    if "gates_max" in want and len(gates) > want["gates_max"]:
        fails.append(f"{len(gates)} gates")
    if "gate_max_m" in want and any(g["width"] > want["gate_max_m"] for g in gates):
        fails.append("opening wider than " + str(want["gate_max_m"]) + " m")
    if "inside_max_m2" in want and fl["closed"] and fl["inside_m2"] > want["inside_max_m2"]:
        fails.append(f"inside {fl['inside_m2']} m2 (> {want['inside_max_m2']}: ring not one street out)")
    if "houses_side_by_side_min" in want and side_by < want["houses_side_by_side_min"] * n_h:
        fails.append(f"{side_by}/{len(houses)} side by side")
    if "houses_touching_max" in want and touching > want["houses_touching_max"]:
        fails.append(f"{touching}/{len(houses)} touch another house")
    if "next_to_field_or_wood_min" in want and near < want["next_to_field_or_wood_min"] * n_h:
        fails.append(f"{near}/{len(houses)} next to field/woods")
    facts["match"] = not fails
    facts["why"] = "; ".join(fails) or "ok"
    return facts


# == round 8+: the town against v1's measures (city_v1.py draws v1's own town on the same timeline) ===========
WALL_GROW = 0.5     # town: pieces grown 0.5 m (cell centres): v1's 1 m block gaps are closed, 2 m is a way out


def rings_of(cc, ps) -> list:
    """Ring index per piece by square distance from the civic centre (the CC's own frame): ring 0 = every piece
    that starts before the outer face of the innermost piece; ring 1 the same over the rest; and so on."""
    din = [R.Map.gap(p["rect"], cc) for p in ps]
    dout = [max(abs(p["rect"][0]) + p["rect"][2], abs(p["rect"][1]) + p["rect"][3]) - cc[2] for p in ps]
    ring, left, k = [None] * len(ps), set(range(len(ps))), 0
    while left:
        first = min(left, key=lambda i: din[i])
        band = [i for i in left if din[i] < dout[first] - 1]
        if len(band) < 3 and len(band) < len(left):       # a stray or two inside the next ring: not a ring
            band = [first]
            ring[first] = -1
            left -= {first}
            continue
        for i in band:
            ring[i] = k
        left -= set(band)
        k += 1
    return ring


def street_gaps(ps, ring) -> list:
    """For each piece of ring 1: its gap to the nearest ring-0 piece (v1: one street, 10 m)."""
    r0 = [p["rect"] for p, k in zip(ps, ring) if k == 0]
    return [round(min(R.Map.gap(p["rect"], q) for q in r0), 1) for p, k in zip(ps, ring) if k == 1 and r0]


def measure_town(cc, ps, want) -> dict:
    """v1's measures on a town (ring pieces = City Planner buildings only; Petra's buildings, fields too, never)."""
    ring = rings_of(cc, ps)
    r0 = [p for p, k in zip(ps, ring) if k == 0]

    def ext(pp):
        return int(max([abs(p["rect"][0]) + p["rect"][2] for p in pp] + [abs(p["rect"][1]) + p["rect"][3] for p in pp] + [45])) + 15
    fl = flood(cc, ps, WALL_GROW, ext(ps), centre=True)      # the city as a wall: every City Planner building
    fl0 = flood(cc, r0, WALL_GROW, ext(r0), centre=True)     # ring 0 alone (reported, not checked)
    gates = fl["gates"]
    gaps = street_gaps(ps, ring)
    lo, hi = want["street_m"]
    ok_gaps = [g for g in gaps if lo <= g <= hi]
    facts = {"buildings": len(ps), "ring0": len(r0), "ring1": ring.count(1), "beyond": sum(k >= 2 or k < 0 for k in ring),
             "closed": fl["closed"], "openings": len(gates), "gates": gates, "street_gaps": gaps,
             "ring0_openings": [(g["width"], g["side"] + (" corner" if g["corner"] else "")) for g in fl0["gates"]],
             "street_ok": f"{len(ok_gaps)}/{len(gaps)}", "ring": ring}
    fails = []
    if want.get("ring_closed") and not fl["closed"]:
        fails.append(f"ring open ({len(gates)}+ ways out)")
    if len(gates) > want["openings_max"]:
        fails.append(f"{len(gates)} openings")
    sides = sorted(g["side"] for g in gates if not g["corner"])
    bad = [g for g in gates if g["side"] not in want["gates_at"] or g["corner"]]
    if bad:
        fails.append("opening at " + ", ".join(f"{g['side']}{' corner' if g['corner'] else ''} ({g['width']} m)" for g in bad))
    missing = [s for s in want["gates_at"] if s not in sides]
    if missing:
        fails.append("no gate at " + ", ".join(missing))
    narrow = [g for g in gates if not want["gate_m"][0] <= g["width"] <= want["gate_m"][1]]
    if narrow:
        fails.append("gate width " + ", ".join(f"{g['width']} m" for g in narrow))
    if not gaps:
        fails.append("no second ring")
    elif len(ok_gaps) < want["street_share_min"] * len(gaps):
        fails.append(f"street between rings {len(ok_gaps)}/{len(gaps)} at {lo}-{hi} m")
    facts["match"] = not fails
    facts["why"] = "; ".join(fails) or "ok"
    return facts


def town_jev(run):
    """The City Planner's final town: its pieces in the CC frame, and the last minute's map."""
    snaps = R.load_all(R.TOWN_LOG)
    hs = {"w": R.PITCH, "d": R.PITCH, "tpl": "house"}
    placed = [{"kind": s["kind"], "rect": tuple(s["rect"])} for s in run["steps"] if s.get("rect")]
    drop = {(d["tpl"], d["x"], d["z"]): d["minute"] for d in run.get("dropped", [])}
    m, _ = R.town_maps(snaps, run["steps"][-1]["minute"], placed, dict(drop), hs)
    return m, placed


def judge_town(run, v1_cache={}) -> dict:
    import city_v1
    want = CHECKS["town"][run["civ"].split()[0]]
    m, ps = town_jev(run)
    jev = measure_town(m.rect(m.cc), ps, want)
    until = run["source"]["until"]
    if until not in v1_cache:
        vm, vps, left, vdrop = city_v1.v1_town(until)
        v1_cache[until] = (vm, vps, left, measure_town(vm.rect(vm.cc), vps, want))
    vm, vps, left, v1 = v1_cache[until]
    out = {"jev": jev, "v1": {**v1, "left_to_petra": left},
           "v1_buildings": [{**R.rect_world(vm, p["rect"], p["kind"]), "ring": p["ring"], "side": p["side"],
                             "minute": p["minute"]} for p in vps]}
    (R.RUNS_DIR / f"{run['id']}.judge.json").write_text(json.dumps(out, indent=1))
    return out


def town_line(rid, j) -> str:
    def one(f):
        g = ", ".join(f"{x['width']} m {x['side']}{' corner' if x['corner'] else ''}" for x in f["gates"])
        return (f"{'MATCH' if f['match'] else 'no'} {f['why']} | openings {f['openings']} [{g}], "
                f"rings {f['ring0']}/{f['ring1']}/+{f['beyond']}, street {f['street_ok']} {f['street_gaps']}, ring 0 alone {f.get('ring0_openings')}")
    return f"{rid}:\n  jev {one(j['jev'])}\n  v1  {one(j['v1'])}"


def latest(n, pattern="*timeline*"):
    fs = sorted((f for f in glob.glob(str(R.RUNS_DIR / f"{pattern}.json")) if not f.endswith(("-dry.json", ".judge.json"))),
                key=os.path.getmtime)[-n:]
    return [os.path.basename(f)[:-5] for f in fs]


def line(rid, j) -> str:
    g = ", ".join(f"{x['width']} m {x['side']}{' corner' if x['corner'] else ''} @{x['out_m']} m ({'/'.join(x['between'])})"
                  for x in j["gates"])
    sl = ", ".join(f"{x['width']} m {x['side']} ({'/'.join(x['between'])})" for x in j["petra_slits"])  # slits < SLIT_M
    return (f"{rid}: {'MATCH' if j['match'] else 'no   '} {j['why']} | closed {j['closed']} "
            f"(inside {j['inside_m2']} m2, reach {j['inside_reach']} m), gates [{g}], slits [{sl}], touching {j['touching']}, side by side {j['side_by_side']}, "
            f"field/wood {j['next_to_field_or_wood']}/{j['houses']}, closed without fields {j['closed_without_fields']}")


def main(argv):
    ids = argv[1:]
    if ids and ids[0] == "--latest":
        ids = latest(int(ids[1]) if len(ids) > 1 else 3)
    for rid in ids:
        run = json.loads((R.RUNS_DIR / f"{rid}.json").read_text())
        print(town_line(rid, judge_town(run)) if run.get("town") else line(rid, judge(run)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
