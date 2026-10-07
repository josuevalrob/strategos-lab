#!/usr/bin/env python3
"""v1's street grid (strategos/mvp1 city.js), ported for the picture and the judge only -- the builder never reads it.

    git -C ~/Projects/0AD show strategos/mvp1:binaries/data/mods/strategos/simulation/ai/strategos/city.js

Same rules as city.js: square grid around the first civic centre, front = the CC axis nearest the nearest enemy
civic centre (alone on the map: the map centre); yard 38, ring street 10, block depth 24, 2 rings, corner 14,
gap 1, gates (10 m streets) at the middle of the front, left and right sides; each side packed from its corners
toward its gate, a hole a building closes on its own first; sides in the order front, left, right, rear, ring by
ring; a deeper-than-block building turns.  "Buildable" here = inside our territory at the centre and 4 corners
(as city.js) and not overlapping a standing structure or a resource (the lab has no passability map).
v1 leaves towers to Petra, so a tower gets no v1 slot.
"""
from __future__ import annotations

import math

import city_real as R

DEFAULTS = {"yard": 38, "street": 10, "depth": 24, "gap": 1, "rings": 2, "corner": 14, "slide": 1, "fill": 3,
            "maxChecks": 800}
ENTRANCES = ["front", "left", "right"]
SIDE_ORDER = ["front", "left", "right", "rear"]
UNPLANNED = {"Tower", "StoneTower", "SentryTower", "Field", "CivCentre", "Wall", "Fortress", "Wonder", "Corral",
             "Dock", "Palisade"}


class V1:
    def __init__(self, m: R.Map, cfg=DEFAULTS):
        self.cfg = cfg
        cc = (m.cc["x"], m.cc["z"])
        enemy = m.enemy
        if enemy:
            front = enemy
        else:
            half = (m.snap.get("map") or 0) / 2
            front = (half, half) if math.hypot(half - cc[0], half - cc[1]) >= 1 else (cc[0] + 1, cc[1])
        dx, dz = front[0] - cc[0], front[1] - cc[1]
        ln = math.hypot(dx, dz)
        u = (dx / ln, dz / ln)
        a, best, bd = m.a, None, -1e9
        for axis in ((math.cos(a), -math.sin(a)), (math.sin(a), math.cos(a))):
            for sg in (1, -1):
                d = sg * (axis[0] * u[0] + axis[1] * u[1])
                if d > bd:
                    bd, best = d, (sg * axis[0], sg * axis[1])
        self.u = best
        self.w = (-best[1], best[0])
        self.cc = cc
        self.sides = []
        neg = lambda v: (-v[0], -v[1])   # noqa: E731
        for ring in range(cfg["rings"]):
            inner = cfg["yard"] + cfg["street"] + ring * (cfg["depth"] + cfg["street"])
            outer = inner + cfg["depth"]
            defs = {"front": (self.u, self.w, outer), "rear": (neg(self.u), self.w, outer),
                    "left": (self.w, self.u, outer - cfg["corner"]), "right": (neg(self.w), self.u, outer - cfg["corner"])}
            for side in SIDE_ORDER:
                n, t, half = defs[side]
                gate = side in ENTRANCES
                self.sides.append({"ring": ring, "side": side, "n": n, "t": t, "inner": inner, "outer": outer,
                                   "lo": -half, "hi": half, "gate": gate,
                                   "taken": [[-cfg["street"] / 2, cfg["street"] / 2]] if gate else []})

    def candidates(self, side, length):
        cfg = self.cfg
        free, cursor = [], side["lo"]
        for lo, hi in sorted(side["taken"]):
            if lo - cursor >= length:
                free.append((cursor, lo))
            cursor = max(cursor, hi)
        if side["hi"] - cursor >= length:
            free.append((cursor, side["hi"]))
        out = []
        for lo, hi in free:
            if hi - lo <= length + cfg["fill"] and (lo > side["lo"] or hi < side["hi"]):
                out.append(((lo + hi) / 2, -1000))
            if side["gate"]:
                if lo + hi >= 0:
                    s = hi - length / 2
                    while s >= lo + length / 2:
                        out.append((s, -abs(s)))
                        s -= cfg["slide"]
                else:
                    s = lo + length / 2
                    while s <= hi - length / 2:
                        out.append((s, -abs(s)))
                        s += cfg["slide"]
            else:
                s = lo + length / 2
                while s <= hi - length / 2:
                    out.append((s, s - side["lo"]))
                    s += cfg["slide"]
        out.sort(key=lambda c: c[1])          # stable, as Array.prototype.sort
        return [c[0] for c in out]

    def rect(self, m: R.Map, side, s, along, across):
        """CC-frame rect (u, v, hu, hv) of a building centred at s on a side, outer face flush."""
        normal = side["outer"] - across / 2
        x = self.cc[0] + side["n"][0] * normal + side["t"][0] * s
        z = self.cc[1] + side["n"][1] * normal + side["t"][1] * s
        u, v = m.loc(x, z)
        t_on_u = abs(side["t"][0] * m.eu[0] + side["t"][1] * m.eu[1]) > 0.5
        return (u, v, along / 2, across / 2) if t_on_u else (u, v, across / 2, along / 2)

    def buildable(self, m: R.Map, r, standing) -> bool:
        if not all(m.in_terr(*m.wld(r[0] + a, r[1] + b)) for a, b in
                   ((0, 0), (-r[2], -r[3]), (r[2], -r[3]), (-r[2], r[3]), (r[2], r[3]))):
            return False
        if any(R.Map.gap(r, q) < 0 for q in standing):
            return False
        return not any(m.point_gap(r, x, z) < R.RES_R[k] for k, x, z in m.res if k in R.RES_R)

    def place(self, m: R.Map, w, d, standing):
        """First free buildable slot for a w x d building: (rect, ring, side) or None (Petra would place it)."""
        cfg, checks = self.cfg, 0
        for ring in range(cfg["rings"]):
            for name in SIDE_ORDER:
                side = next(s for s in self.sides if s["ring"] == ring and s["side"] == name)
                along, across = w, d
                if across > cfg["depth"]:
                    along, across = d, w
                if across > cfg["depth"]:
                    continue
                for s in self.candidates(side, along + cfg["gap"]):
                    checks += 1
                    if checks > cfg["maxChecks"]:
                        return None
                    r = self.rect(m, side, s, along, across)
                    if self.buildable(m, r, standing):
                        side["taken"].append([s - (along + cfg["gap"]) / 2, s + (along + cfg["gap"]) / 2])
                        return r, ring, name
        return None


def v1_town(until: int):
    """v1's town on the same timeline the City Planner gets: the same queued buildings at the same minutes, Petra's
    other structures where she put them (dropped when a v1 building stands there).  Returns (final Map, pieces,
    left_to_petra)."""
    snaps = R.load_all(R.TOWN_LOG)
    events = R.queue_events(R.TOWN_LOG, until)
    hs = {"w": R.PITCH, "d": R.PITCH, "tpl": "house"}
    placed, dropped, left, v1, m = [], {}, [], None, None
    for ev in events:
        m, _ = R.town_maps(snaps, ev["minute"], placed, dropped, hs)
        if v1 is None:
            v1 = V1(m)
        if ev["cls"] in UNPLANNED:
            left.append(ev["kind"])
            continue
        standing = [m.rect(s) for s in m.structs] + [p["rect"] for p in placed]
        got = v1.place(m, ev["w"], ev["d"], standing)
        if got is None:
            left.append(ev["kind"])
            continue
        placed.append({"kind": ev["kind"], "rect": got[0], "ring": got[1], "side": got[2], "minute": ev["minute"]})
    m, _ = R.town_maps(snaps, events[-1]["minute"], placed, dropped, hs)
    return m, placed, left, dropped
