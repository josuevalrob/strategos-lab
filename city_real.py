#!/usr/bin/env python3
"""Real-map city planner: Jev places houses on a 0 A.D. snapshot, one house per question.

    python3 city_real.py --civ spart [--runs 3] [--houses 12] [--minute 10]
    python3 city_real.py --civ germ --dry            # no Jev: prompts + a fake run for the page
    python3 city_real.py --civ iber --print 3        # print the prompt + options of question 3 (dry)

Start state = the minute-N `[strategos] snapshot` of a Strategos engine.log (read only) WITHOUT its houses.
Candidate slots = a 14 m lattice aligned to the civic centre; code removes only impossible slots (outside
our territory, overlapping a structure, a resource or a house) and keeps ONE locality rule (within one
street of a building).  The goal is the civ JSON layout line, verbatim; nothing here knows a target shape.
Facts in state/options are shape-agnostic and only those the goal talks about (fields, woods, ...).
Asks go through the running lab (POST /api/ask) via city_demo.ask; Jev's choice is used as given.
Runs land in static/city/runs/<id>.json (mode "real"), shown by /static/city.html.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import city_demo as cd

REPO0AD = Path("~/Projects/0AD").expanduser()
LOG = REPO0AD / ".claude/strategos/runs/solo/20261006-213701/engine.log"
CIVS = REPO0AD / "binaries/data/mods/strategos/simulation/data/strategos/civs"
LAYOUT_LINE = 5                     # civ JSON text[5] = the town layout line
RUNS_DIR = cd.RUNS_DIR
PITCH = 14                          # lattice step = house width: neighbours stand shoulder to shoulder
STREET = 10                         # "a street is about 10 m"
REACH = 10                          # locality rule: within one street of a building
VARIANT = {"name": "real1", "around": False, "pieces": False}   # real2: around/away; pieces: Petra's buildings count
TREE_R, MINE_R = 1.5, 6.0           # obstruction radius of a tree / a mine


# == snapshot ===================================================================================
def load_snapshot(path: Path, minute: int) -> dict:
    for line in path.read_text(errors="replace").splitlines():
        i = line.find("[strategos] snapshot ")
        if i >= 0:
            snap = json.loads(line[i + len("[strategos] snapshot "):])
            if snap.get("m") == minute:
                return snap
    raise SystemExit(f"no snapshot for minute {minute} in {path}")


def is_house(s) -> bool:
    return s["tpl"].endswith("/house")


def short(tpl: str) -> str:
    return tpl.split("/")[-1].replace("_", " ").replace("civil centre", "civic centre")


class Map:
    """Geometry in the civic centre's own frame: u along its local x (right flank), v along its facing."""

    def __init__(self, snap: dict, house_size=None):
        self.snap = snap
        houses = [s for s in snap["structures"] if is_house(s)]
        self.structs = [s for s in snap["structures"] if not is_house(s)]
        self.cc = next(s for s in self.structs if "civil_centre" in s["tpl"])
        self.a = self.cc["a"]
        self.eu = (math.cos(self.a), -math.sin(self.a))
        self.ev = (math.sin(self.a), math.cos(self.a))
        hw = houses[0] if houses else (house_size or {"w": 14, "d": 14, "tpl": "house"})
        self.house_w, self.house_d = hw["w"], hw["d"]
        self.house_tpl = hw["tpl"]
        t = snap["territory"]
        self.terr = t
        self.grid = ["".join(f"{int(ch, 16):04b}" for ch in r)[:t["w"]] for r in t["rows"]]
        self.res = [(k, x, z) for k, pts in snap["resources"].items() if isinstance(pts, list) for x, z, _ in pts]
        self.trees = [(x, z) for k, x, z in self.res if k == "wood"]
        enemy = snap.get("enemy_cc") or []
        self.enemy = (enemy[0], enemy[1]) if len(enemy) >= 2 and not isinstance(enemy[0], list) else (
            tuple(enemy[0][:2]) if enemy and isinstance(enemy[0], list) else None)

    # frames
    def loc(self, x, z):
        dx, dz = x - self.cc["x"], z - self.cc["z"]
        return dx * self.eu[0] + dz * self.eu[1], dx * self.ev[0] + dz * self.ev[1]

    def wld(self, u, v):
        return (self.cc["x"] + u * self.eu[0] + v * self.ev[0], self.cc["z"] + u * self.eu[1] + v * self.ev[1])

    def in_terr(self, x, z) -> bool:
        t = self.terr
        i, j = int(x // t["cell"]) - t["x0"], int(z // t["cell"]) - t["z0"]
        return 0 <= j < len(self.grid) and 0 <= i < t["w"] and self.grid[j][i] == "1"

    def edge_dist(self, x, z) -> float:
        """Metres from (x, z) to the nearest cell outside our territory."""
        t, c = self.terr, self.terr["cell"]
        best = 1e9
        for j in range(-1, len(self.grid) + 1):
            for i in range(-1, t["w"] + 1):
                inside = 0 <= j < len(self.grid) and 0 <= i < t["w"] and self.grid[j][i] == "1"
                if not inside:
                    cx, cz = (i + t["x0"] + 0.5) * c, (j + t["z0"] + 0.5) * c
                    best = min(best, math.hypot(cx - x, cz - z) - c / 2)
        return max(0.0, best)

    # footprints, in the CC frame (every structure in these snapshots shares the CC angle; others are
    # approximated by their bounding square in this frame)
    def rect(self, s):
        u, v = self.loc(s["x"], s["z"])
        if abs(((s["a"] - self.a + math.pi) % (2 * math.pi)) - math.pi) < 0.05 or \
                abs(((s["a"] - self.a) % math.pi)) < 0.05:
            return (u, v, s["w"] / 2, s["d"] / 2)
        r = math.hypot(s["w"], s["d"]) / 2
        return (u, v, r, r)

    @staticmethod
    def gap(r1, r2) -> float:
        return max(abs(r1[0] - r2[0]) - r1[2] - r2[2], abs(r1[1] - r2[1]) - r1[3] - r2[3])

    def point_gap(self, r, x, z) -> float:
        u, v = self.loc(x, z)
        return max(abs(u - r[0]) - r[2], abs(v - r[1]) - r[3])


# == words (shape-agnostic) ====================================================================
COMPASS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


def compass(dx, dz) -> str:
    return COMPASS[round((math.degrees(math.atan2(dx, dz)) % 360) / 45) % 8]


def num(n):
    return cd.num(n)


def metres(g) -> str:
    return f"{round(g)} m"


class Words:
    def __init__(self, m: Map):
        self.m = m
        eu, ev = m.eu, m.ev
        self.face = {"front": compass(*ev), "back": compass(-ev[0], -ev[1]),
                     "right flank": compass(*eu), "left flank": compass(-eu[0], -eu[1])}

    def side(self, u, v) -> str:
        """Which side of the civic centre a slot looks at (by the CC's own faces), or which corner."""
        au, av = abs(u), abs(v)
        if abs(au - av) < PITCH / 2 and au > 15:
            x, z = (u > 0) - (u < 0), (v > 0) - (v < 0)
            d = (x * self.m.eu[0] + z * self.m.ev[0], x * self.m.eu[1] + z * self.m.ev[1])
            return f"off the {compass(*d)} corner of the civic centre"
        if av >= au:
            k = "front" if v > 0 else "back"
        else:
            k = "right flank" if u > 0 else "left flank"
        return f"on the {self.face[k]} side of the civic centre (its {k})"

    def cc_gap(self, g) -> str:
        if g <= 0.5:
            return "touches the civic centre"
        if g <= 15:
            return f"one street ({metres(g)}) from the civic centre"
        if g <= 30:
            return f"{metres(g)} from the civic centre, more than one street"
        return f"{metres(g)} from the civic centre, far from it"


# == the builder =================================================================================
class Builder:
    """Slots, state and options from the map + houses so far.  Never sees a target."""

    def __init__(self, m: Map, goal: str, houses=()):
        self.m, self.w, self.goal = m, Words(m), goal.lower()
        self.blocks = [m.rect(s) for s in m.structs]
        self.cc_rect = m.rect(m.cc)
        self.fields = [m.rect(s) for s in m.structs if s["tpl"].endswith("/field")]
        self.stores = [m.rect(s) for s in m.structs if "storehouse" in s["tpl"]]
        self.h = PITCH / 2
        self.slots = self._free_slots()
        self.slots.update({k: (k[0] * PITCH, k[1] * PITCH, self.h, self.h) for k in houses})
        self.mentions = {k: any(w in self.goal for w in ws) for k, ws in {
            "field": ["field"], "wood": ["wood"], "storehouse": ["storehouse"],
            "open": ["open ground", "apart"], "edge": ["border", "edge of", "empty land"],
            "enemy": ["enemy"]}.items()}

    # -- slots ----------------------------------------------------------------------------------
    def _free_slots(self) -> dict:
        m, h, out = self.m, self.h, {}
        for i in range(-20, 21):
            for j in range(-20, 21):
                u, v = i * PITCH, j * PITCH
                r = (u, v, h, h)
                pts = [m.wld(u + a, v + b) for a in (-h, 0, h) for b in (-h, 0, h)]
                if not all(m.in_terr(x, z) for x, z in pts):
                    continue
                if any(Map.gap(r, b) < 0 for b in self.blocks):
                    continue
                if any(m.point_gap(r, x, z) < (TREE_R if k == "wood" else MINE_R) for k, x, z in m.res):
                    continue
                out[(i, j)] = r
        return out

    def candidates(self, houses: list) -> list:
        """Free slots (not overlapping a house) within one street of a building or a house."""
        hs = {k: self.slots[k] for k in houses}
        near = self.blocks + list(hs.values())
        out = []
        for k, r in self.slots.items():
            if any(Map.gap(r, hr) < 0 for hr in hs.values()):
                continue
            if min(Map.gap(r, b) for b in near) <= REACH:
                out.append(k)
        out.sort(key=lambda k: (Map.gap(self.slots[k], self.cc_rect), k))
        return out

    # -- facts ----------------------------------------------------------------------------------
    def runs(self, houses) -> list:
        hs = set(houses)
        out = []
        for d in ((1, 0), (0, 1)):
            for c in sorted(hs):
                if (c[0] - d[0], c[1] - d[1]) in hs:
                    continue
                cells = [c]
                while (cells[-1][0] + d[0], cells[-1][1] + d[1]) in hs:
                    cells.append((cells[-1][0] + d[0], cells[-1][1] + d[1]))
                if len(cells) >= 2:
                    out.append((d, cells))
        longc = {c for _, cells in out if len(cells) >= 3 for c in cells}
        return [(d, cells) for d, cells in out if len(cells) >= 3 or not set(cells) <= longc]

    def dir_word(self, d) -> str:
        e = self.m.eu if d[0] else self.m.ev
        s = 1 if (d[0] or d[1]) > 0 else -1
        return compass(e[0] * s, e[1] * s)

    def line_name(self, d, cells) -> str:
        a, b = self.dir_word((-d[0], -d[1])), self.dir_word(d)
        rs = [self.slots[c] for c in cells]
        mu, mv = sum(r[0] for r in rs) / len(rs), sum(r[1] for r in rs) / len(rs)
        where = self.w.side(mu, mv).split(" (")[0]          # the side its middle looks at
        g = min(Map.gap(r, self.cc_rect) for r in rs)
        rel = ""
        if VARIANT["around"] and abs(abs(mu) - abs(mv)) >= PITCH / 2:
            along_face = (d[0] != 0) == (abs(mv) > abs(mu))     # parallel to the CC face it looks at
            rel = " around the civic centre" if along_face else " away from the civic centre"
        return f"line running {a} to {b}{rel} {where}, {self.w.cc_gap(g)}"

    def end_name(self, d, cells, c) -> str:
        return f"{self.dir_word((-d[0], -d[1])) if c == cells[0] else self.dir_word(d)} end"

    def touch(self, k, houses) -> str:
        if not houses:
            return "the first house"
        side_by = sum((k[0] + a, k[1] + b) in houses for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        if side_by:
            return f"shoulder to shoulder with {num(side_by)} house{'s' if side_by > 1 else ''}"
        if any((k[0] + a, k[1] + b) in houses for a, b in ((1, 1), (1, -1), (-1, 1), (-1, -1))):
            return "touches a house only at a corner"
        r = self.slots[k]
        g = min(Map.gap(r, self.slots[h]) for h in houses)
        return (f"stands apart, one street ({metres(g)}) from the nearest house" if g <= 15
                else f"stands apart, {metres(g)} from the nearest house")

    def anchor(self, k, houses) -> list:
        hs = set(houses)
        rs = self.runs(hs)
        out = []
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (k[0] - d[0], k[1] - d[1])
            if n not in hs:
                continue
            same = [(ax, cells) for ax, cells in rs if n in cells and ax in (d, (-d[0], -d[1]))]
            if same:
                ax, cells = same[0]
                out.append(f"extends toward the {self.dir_word(d)} the {self.line_name(ax, cells)}")
                continue
            ends = [(ax, cells) for ax, cells in rs if n in (cells[0], cells[-1])]
            if ends:
                ax, cells = ends[0]
                r = self.slots[k]
                rel = ""
                if VARIANT["around"] and abs(abs(r[0]) - abs(r[1])) >= PITCH / 2:
                    rel = (" around the civic centre" if (d[0] != 0) == (abs(r[1]) > abs(r[0]))
                           else " away from the civic centre")
                out.append(f"turns {self.dir_word(d)}{rel} at the {self.end_name(ax, cells, n)} of the "
                           f"{self.line_name(ax, cells)}")
            else:
                r = self.slots[n]
                rel = ""
                if VARIANT["around"] and abs(abs(r[0]) - abs(r[1])) >= PITCH / 2:
                    along_face = (d[0] != 0) == (abs(r[1]) > abs(r[0]))
                    rel = " around the civic centre" if along_face else " away from the civic centre"
                out.append(f"starts a line toward the {self.dir_word(d)}{rel} from the single house "
                           f"{self.w.side(r[0], r[1])}")
        for key in ("extends", "turns", "starts"):
            hit = [p for p in out if p.startswith(key)]
            if hit:
                return hit[:2]
        return []

    def near_facts(self, k, houses) -> list:
        m, r, out = self.m, self.slots[k], []
        if self.mentions["field"] and self.fields:
            g, f = min((Map.gap(r, f), f) for f in self.fields)
            if g <= 6:
                own = not any(Map.gap(self.slots[h], f) <= 6 for h in houses)
                out.append(f"next to a field ({metres(max(g, 0))})" +
                           (", a field with no house beside it yet" if own else ", a field that already has a house beside it"))
            else:
                out.append(f"{metres(g)} from the nearest field")
        if self.mentions["wood"] and m.trees:
            g = min(m.point_gap(r, x, z) for x, z in m.trees)
            out.append(f"at the edge of the woods ({metres(g)} to the nearest tree)" if g <= 10
                       else f"{metres(g)} from the nearest trees")
        if self.mentions["storehouse"] and self.stores:
            g = min(Map.gap(r, s) for s in self.stores)
            out.append(f"next to a storehouse ({metres(max(g, 0))})" if g <= 10 else f"{metres(g)} from the nearest storehouse")
        if self.mentions["open"]:
            things = [Map.gap(r, b) for b in self.blocks] + [Map.gap(r, self.slots[h]) for h in houses] + \
                     [m.point_gap(r, x, z) for x, z in m.trees]
            out.append("open ground all around it" if min(things) > 8 else "close to other buildings or trees")
        if self.mentions["edge"]:
            x, z = m.wld(r[0], r[1])
            e = m.edge_dist(x, z)
            out.append(f"at the edge of our territory ({metres(e)})" if e <= 20 else f"{metres(e)} inside our territory")
        if self.mentions["enemy"] and m.enemy:
            x, z = m.wld(r[0], r[1])
            ex, ez = m.enemy
            cx, cz = m.cc["x"], m.cc["z"]
            cos = ((x - cx) * (ex - cx) + (z - cz) * (ez - cz)) / (math.hypot(x - cx, z - cz) * math.hypot(ex - cx, ez - cz) + 1e-9)
            out.append("on the side toward the enemy" if cos > 0.7 else "on the side away from the enemy" if cos < -0.7
                       else "beside the civic centre, not toward the enemy")
        return out

    def option_text(self, k, houses) -> str:
        r = self.slots[k]
        parts = [self.w.side(r[0], r[1]), self.w.cc_gap(Map.gap(r, self.cc_rect)), self.touch(k, houses)]
        parts += self.anchor(k, houses) + self.near_facts(k, houses)
        return "a house " + "; ".join(parts)

    # -- state ----------------------------------------------------------------------------------
    def map_lines(self) -> list:
        m, w, out = self.m, self.w, []
        out.append(f"Our civic centre: its front faces {w.face['front']}, its flanks face {w.face['left flank']} "
                   f"and {w.face['right flank']}, its back faces {w.face['back']}.")
        if self.mentions["field"] and self.fields:
            by = {}
            for f in self.fields:
                by.setdefault(w.side(f[0], f[1]).split(" (")[0], []).append(f)
            out.append(f"Fields: {num(len(self.fields))}: " + "; ".join(
                f"{num(len(v))} {k}" for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))) + ".")
        if self.mentions["wood"] and m.trees:
            near = sorted((Map.gap(self.cc_rect, (u, v, 0, 0)), compass(x - m.cc["x"], z - m.cc["z"]))
                          for x, z in m.trees for u, v in [m.loc(x, z)])
            seen, parts = set(), []
            for g, c in near:
                if c not in seen:
                    seen.add(c)
                    parts.append(f"{c} of the civic centre ({metres(g)})")
            out.append("Woods, nearest first: " + ", ".join(parts[:4]) + ".")
        if self.mentions["storehouse"] and self.stores:
            out.append("Storehouses: " + "; ".join(f"{w.side(s[0], s[1]).split(' (')[0]}, {w.cc_gap(Map.gap(s, self.cc_rect))}"
                                                   for s in self.stores) + ".")
        if self.mentions["enemy"]:
            out.append("Enemy: " + (f"his civic centre lies {compass(m.enemy[0] - m.cc['x'], m.enemy[1] - m.cc['z'])} of ours."
                                    if m.enemy else "not on this map; his direction is not known."))
        return out

    def state(self, houses) -> str:
        lines = self.map_lines() + [f"Houses built so far: {num(len(houses)) if houses else 'none yet'}."]
        if houses:
            rs = self.runs(houses)
            parts = [f"a {self.line_name(d, cells)}, {num(len(cells))} houses long" for d, cells in
                     sorted(rs, key=lambda r: -len(r[1]))]
            in_run = {c for _, cells in rs for c in cells}
            singles = [h for h in houses if h not in in_run]
            s = "Lines of houses so far: " + ("; ".join(parts) if parts else "none") + "."
            if singles:
                s += " Single houses: " + "; ".join(
                    f"one {self.w.side(*self.slots[h][:2])}, {self.w.cc_gap(Map.gap(self.slots[h], self.cc_rect))}"
                    for h in singles) + "."
            lines.append(s)
        return "\n".join(lines)


# == round 6: Petra's buildings are pieces of lines and rings too ===============================
TOL = 3.0          # pieces closer than this (m) count as touching
NEAR_CC = 30.0     # a piece this close to the civic centre (gap, m) lines one of its sides
ARTICLE = {"house": "a house", "field": "a field", "barracks": "a barracks", "storehouse": "a storehouse",
           "farmstead": "a farmstead", "stable": "a stable"}


def kind_list(kinds) -> str:
    """['house','house','field'] -> 'two houses and a field' (houses first, then the rest by name)."""
    cnt = {}
    for k in kinds:
        cnt[k] = cnt.get(k, 0) + 1
    parts = []
    for k in sorted(cnt, key=lambda k: (k != "house", k)):
        n = cnt[k]
        parts.append(ARTICLE.get(k, "a " + k) if n == 1 else f"{num(n)} {k}{'' if k.endswith('s') else 's'}")
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def inline(a, b, ax) -> bool:
    """a and b (u, v, hu, hv) touch along axis ax (0 = u, 1 = v) and face each other across most of a side."""
    o = 1 - ax
    gap = abs(a[ax] - b[ax]) - a[2 + ax] - b[2 + ax]
    overlap = min(a[o] + a[2 + o], b[o] + b[2 + o]) - max(a[o] - a[2 + o], b[o] - b[2 + o])
    return -0.5 <= gap <= TOL and overlap >= 0.6 * min(2 * a[2 + o], 2 * b[2 + o])


class PiecesBuilder(Builder):
    """Lines, rings and touching are made of Jev's houses AND every structure Petra placed (not the CC)."""

    def pieces(self, houses, extra=None) -> list:
        out = [{"kind": short(s["tpl"]), "rect": self.m.rect(s)} for s in self.m.structs if "civil_centre" not in s["tpl"]]
        out += [{"kind": "house", "rect": self.slots[k], "key": k} for k in houses]
        if extra is not None:
            out.append({"kind": "new", "rect": self.slots[extra], "key": extra})
        return out

    @staticmethod
    def comps(ps, edge) -> list:
        seen, out = set(), []
        for i in range(len(ps)):
            if i in seen:
                continue
            stack, comp = [i], []
            seen.add(i)
            while stack:
                a = stack.pop()
                comp.append(a)
                for b in range(len(ps)):
                    if b not in seen and edge(ps[a]["rect"], ps[b]["rect"]):
                        seen.add(b)
                        stack.append(b)
            out.append(comp)
        return out

    def lines(self, ps) -> list:
        """Straight lines of 2+ touching pieces: [(axis, [piece indices sorted along the axis])]."""
        out = []
        for ax in (0, 1):
            for comp in self.comps(ps, lambda a, b, ax=ax: inline(a, b, ax)):
                if len(comp) >= 2:
                    out.append((ax, sorted(comp, key=lambda i: ps[i]["rect"][ax])))
        return out

    def pline_name(self, ax, idx, ps) -> str:
        d = (1, 0) if ax == 0 else (0, 1)
        a, b = self.dir_word((-1, 0) if ax == 0 else (0, -1)), self.dir_word(d)
        rs = [ps[i]["rect"] for i in idx]
        mu, mv = sum(r[0] for r in rs) / len(rs), sum(r[1] for r in rs) / len(rs)
        g = min(Map.gap(r, self.cc_rect) for r in rs)
        kinds = [ps[i]["kind"] for i in idx if ps[i]["kind"] != "new"]
        return (f"line of {kind_list(kinds)} running {a} to {b} {self.w.side(mu, mv).split(' (')[0]}, "
                f"{self.w.cc_gap(g)}")

    def faces(self, rects) -> list:
        """Sides of the civic centre lined by these pieces (only pieces near it)."""
        got = set()
        for r in rects:
            if Map.gap(r, self.cc_rect) > NEAR_CC:
                continue
            au, av = abs(r[0]), abs(r[1])
            if av >= au - PITCH / 2:
                got.add("front" if r[1] > 0 else "back")
            if au >= av - PITCH / 2:
                got.add("right flank" if r[0] > 0 else "left flank")
        return [f for f in ("front", "left flank", "back", "right flank") if f in got]

    @staticmethod
    def face_list(fs) -> str:
        if len(fs) == 4:
            return "all four sides"
        fs = [f"the {f}" for f in fs]
        return fs[0] if len(fs) == 1 else ", ".join(fs[:-1]) + " and " + fs[-1]

    def touch(self, k, houses) -> str:
        ps = self.pieces(houses)
        r = self.slots[k]
        side_by = [p["kind"] for p in ps if inline(r, p["rect"], 0) or inline(r, p["rect"], 1)]
        if side_by:
            return "shoulder to shoulder with " + kind_list(side_by)
        return super().touch(k, houses)

    def anchor(self, k, houses) -> list:
        ps = self.pieces(houses)
        r = self.slots[k]
        ls = self.lines(ps)
        out = []
        for ax in (0, 1):
            plus = [i for i, p in enumerate(ps) if inline(r, p["rect"], ax) and p["rect"][ax] > r[ax]]
            minus = [i for i, p in enumerate(ps) if inline(r, p["rect"], ax) and p["rect"][ax] < r[ax]]

            def line_of(i):
                return next(((a, idx) for a, idx in ls if a == ax and i in idx), None)
            if plus and minus:
                la, lb = line_of(plus[0]), line_of(minus[0])
                na = self.pline_name(*la, ps) if la else f"{ARTICLE.get(ps[plus[0]]['kind'])}"
                nb = self.pline_name(*lb, ps) if lb else f"{ARTICLE.get(ps[minus[0]]['kind'])}"
                out.append(f"closes the gap between the {nb} and the {na}" if la or lb else
                           f"closes the gap between {nb} and {na}")
                continue
            for side_i, d in ((plus, -1), (minus, 1)):
                if not side_i:
                    continue
                dvec = (d, 0) if ax == 0 else (0, d)
                l = line_of(side_i[0])
                if l:
                    out.append(f"extends toward the {self.dir_word(dvec)} the {self.pline_name(*l, ps)}")
                else:
                    turn = [(a, idx) for a, idx in ls if a != ax and side_i[0] in (idx[0], idx[-1])]
                    p = ps[side_i[0]]
                    if turn:
                        a2, idx2 = turn[0]
                        end = ("first" if side_i[0] == idx2[0] else "last")
                        endw = self.dir_word(((-1, 0) if a2 == 0 else (0, -1)) if end == "first" else ((1, 0) if a2 == 0 else (0, 1)))
                        out.append(f"turns {self.dir_word(dvec)} at the {endw} end of the {self.pline_name(a2, idx2, ps)}")
                    else:
                        out.append(f"starts a line toward the {self.dir_word(dvec)} from {ARTICLE.get(p['kind'], p['kind'])} "
                                   f"{self.w.side(p['rect'][0], p['rect'][1]).split(' (')[0]}")
        ps2 = self.pieces(houses, extra=k)
        band = next(c for c in self.comps(ps2, lambda a, b: Map.gap(a, b) <= TOL) if len(ps2) - 1 in c)
        if len(band) >= 2:
            before = self.faces([ps2[i]["rect"] for i in band if i != len(ps2) - 1])
            after = self.faces([ps2[i]["rect"] for i in band])
            if after:
                out.append(f"joins touching buildings that then line {self.face_list(after)} of the civic centre"
                           + (f" (before: {self.face_list(before)})" if before and before != after else ""))
        return out[:3]

    def state(self, houses) -> str:
        lines = self.map_lines() + [f"Houses built so far: {num(len(houses)) if houses else 'none yet'}."]
        ps = self.pieces(houses)
        ls = sorted(self.lines(ps), key=lambda l: -len(l[1]))
        if ls:
            lines.append("Lines of buildings so far (houses, fields and Petra's other buildings): " +
                         "; ".join(f"a {self.pline_name(ax, idx, ps)}" for ax, idx in ls[:8]) + ".")
        bands = [c for c in self.comps(ps, lambda a, b: Map.gap(a, b) <= TOL) if len(c) >= 2]
        lined = [(self.faces([ps[i]["rect"] for i in c]), c) for c in bands]
        lined = [(f, c) for f, c in lined if f]
        if lined:
            lines.append("Touching buildings along the civic centre: " + "; ".join(
                f"{kind_list([ps[i]['kind'] for i in c])} touching each other, lining {self.face_list(f)}"
                for f, c in sorted(lined, key=lambda x: -len(x[0]))) + ".")
        return "\n".join(lines)


TEMPLATE = """Role: City planner.
Goal: {goal}
Map: a real map seen from above. One house is {hw} m wide; a street is about {street} m wide.
{state}
Question {n} of {total}: where does the next house go?"""
INSTRUCTIONS = "Where does the next house go?"


def build(b: Builder, goal: str, houses: list, n: int, total: int):
    cands = b.candidates(houses)
    options = [{"id": f"s{i:+d}_{j:+d}".replace("+", "p").replace("-", "m"), "slot": (i, j),
                "text": b.option_text((i, j), houses)} for i, j in cands]
    prompt = TEMPLATE.format(goal=goal, hw=round(b.m.house_w), street=STREET, state=b.state(houses), n=n, total=total)
    return prompt, options


# == per-run facts (no target: what Josue looks at) ============================================
def run_facts(b: Builder, houses: list) -> dict:
    hs = set(houses)
    touching = sum(any((k[0] + a, k[1] + c) in hs for a, c in ((1, 0), (-1, 0), (0, 1), (0, -1))) for k in houses)
    gaps = [Map.gap(b.slots[k], b.cc_rect) for k in houses]
    lines3 = [len(c) for _, c in b.runs(houses) if len(c) >= 3]
    near_field = sum(min((Map.gap(b.slots[k], f) for f in b.fields), default=99) <= 6 for k in houses)
    near_wood = sum(min((b.m.point_gap(b.slots[k], x, z) for x, z in b.m.trees), default=99) <= 10 for k in houses)
    sides = {}
    for k in houses:
        s = b.w.side(*b.slots[k][:2]).split(" (")[0]
        sides[s] = sides.get(s, 0) + 1
    return {"houses": len(houses), "touching_another": touching, "lines_3plus": lines3,
            "gap_cc_min": round(min(gaps), 1) if gaps else None,
            "gap_cc_mean": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "next_to_field": near_field, "next_to_woods": near_wood, "by_side": sides}


# == run ========================================================================================
def map_payload(m: Map) -> dict:
    t = m.terr
    return {"cc": {"x": m.cc["x"], "z": m.cc["z"], "a": m.a}, "map_size": m.snap.get("map"),
            "structures": [{k: s[k] for k in ("tpl", "x", "z", "a", "w", "d")} for s in m.structs],
            "resources": [[k, x, z] for k, x, z in m.res],
            "territory": {"cell": t["cell"], "x0": t["x0"], "z0": t["z0"], "w": t["w"], "grid": m.grid},
            "house": {"w": m.house_w, "d": m.house_d}, "pitch": PITCH}


def slot_world(b: Builder, k):
    x, z = b.m.wld(k[0] * PITCH, k[1] * PITCH)
    return [round(x, 2), round(z, 2)]


def save_real(run: dict):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{run['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(run, indent=1))
    tmp.replace(path)
    idx_path = RUNS_DIR / "index.json"
    try:
        index = json.loads(idx_path.read_text())
    except (OSError, ValueError):
        index = []
    index = [e for e in index if e.get("id") != run["id"]]
    index.append({"id": run["id"], "mode": "real", "variant": run["variant"], "goal": run["civ"],
                  "tier": "civ line", "dry": run.get("dry", False), "started": run["started"], **run["summary"]})
    index.sort(key=lambda e: e["id"], reverse=True)
    tmp = idx_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=1))
    tmp.replace(idx_path)


def civ_line(civ: str) -> str:
    return json.loads((CIVS / f"{civ}.json").read_text())["text"][LAYOUT_LINE]


def one_run(civ: str, minute: int, total: int, dry: bool, goal_text: str | None = None) -> dict:
    snap = load_snapshot(LOG, minute)
    m = Map(snap)
    goal = goal_text or civ_line(civ)
    b = Builder(m, goal)
    now = datetime.now()
    run = {"id": f"{now:%Y%m%d-%H%M%S}-{VARIANT['name']}-{civ}" + ("-prop" if goal_text else "") + ("-dry" if dry else ""),
           "mode": "real", "variant": VARIANT["name"], "civ": civ + (" (proposed line)" if goal_text else ""), "dry": dry, "goal_text": goal, "started": now.isoformat(timespec="seconds"),
           "source": {"log": str(LOG), "minute": minute}, "template": TEMPLATE, "instructions": INSTRUCTIONS,
           "rule": f"free {PITCH} m lattice slots aligned to the civic centre, within {REACH} m of a building or a house",
           "facts_used": [k for k, v in b.mentions.items() if v], "map": map_payload(m),
           "slots_all": {f"{i},{j}": slot_world(b, (i, j)) for i, j in b.slots}, "steps": [], "summary": {}}
    houses: list = []
    for n in range(1, total + 1):
        prompt, options = build(b, goal, houses, n, total)
        if not options:
            run["summary"]["error"] = f"q{n}: no free slot left"
            break
        byid = {o["id"]: o for o in options}
        t0 = time.time()
        if dry:
            r = {"ok": True, "choice": options[0]["id"], "probabilities": {o["id"]: (1.0 if i == 0 else 0.0)
                                                                          for i, o in enumerate(options)}}
        else:
            r = cd.ask_retry(prompt, options, n)
        if not r.get("ok"):
            run["summary"]["error"] = f"q{n}: {r.get('error')}"
            run["steps"].append({"n": n, "prompt": prompt, "error": r.get("error")})
            break
        k = byid[r["choice"]]["slot"]
        houses.append(k)
        probs = r.get("probabilities") or {}
        top = sorted(probs, key=lambda i: -probs[i])[:5]
        run["steps"].append({
            "n": n, "prompt": prompt, "n_options": len(options),
            "slots": {o["id"]: slot_world(b, o["slot"]) for o in options},
            "top": [{"id": i, "p": probs[i], "text": byid[i]["text"]} for i in top if i in byid],
            "choice": r["choice"], "text": byid[r["choice"]]["text"], "p": probs.get(r["choice"]),
            "house": slot_world(b, k), "ms": r.get("ms") or round((time.time() - t0) * 1000)})
        run["summary"] = run_facts(b, houses)
        save_real(run)
        if cd.VERBOSE:
            print(f"q{n:2d}: {len(options):2d} opts -> {r['choice']} p={probs.get(r['choice'])}: {byid[r['choice']]['text'][:150]}")
    run["summary"] = {**run_facts(b, houses), **({"error": run["summary"]["error"]} if run["summary"].get("error") else {})}
    run["houses"] = [slot_world(b, k) for k in houses]
    save_real(run)
    s = run["summary"]
    print(f"run {run['id']}: {s['houses']} houses, touching another {s['touching_another']}, lines>=3 {s['lines_3plus']}, "
          f"gap to CC min/mean {s['gap_cc_min']}/{s['gap_cc_mean']} m, next to field {s['next_to_field']}, "
          f"next to woods {s['next_to_woods']}, by side {s['by_side']}" + (f", error: {s['error']}" if s.get("error") else ""))
    return run


# == real timeline: question k is asked at the minute Petra's k-th house first appears ==========
def load_all(path: Path) -> dict:
    out = {}
    for line in path.read_text(errors="replace").splitlines():
        i = line.find("[strategos] snapshot ")
        if i >= 0:
            snap = json.loads(line[i + len("[strategos] snapshot "):])
            out[snap["m"]] = snap
    return out


def petra_house_minutes(snaps: dict) -> tuple:
    """First minute each Petra house appears (foundation or finished), by position: a finished building
    gets a new entity id, so ids do not identify it."""
    first, size = {}, None
    for mnt in sorted(snaps):
        for st in snaps[mnt]["structures"]:
            if is_house(st):
                size = size or {"w": st["w"], "d": st["d"], "tpl": st["tpl"]}
                first.setdefault((st["x"], st["z"]), mnt)
    return sorted(first.values()), size


def skey(st) -> tuple:
    return (st["tpl"], st["x"], st["z"])


def timeline_run(civ: str, dry: bool, goal_text: str | None = None) -> dict:
    snaps = load_all(LOG)
    minutes, hsize = petra_house_minutes(snaps)
    goal = goal_text or civ_line(civ)
    now = datetime.now()
    run = {"id": f"{now:%Y%m%d-%H%M%S}-timeline{'2' if VARIANT['pieces'] else ''}-{civ}" + ("-prop" if goal_text else "") + ("-dry" if dry else ""),
           "mode": "real", "timeline": True, "variant": "timeline2" if VARIANT["pieces"] else "timeline1", "civ": civ + (" (proposed line)" if goal_text else ""),
           "dry": dry, "goal_text": goal, "started": now.isoformat(timespec="seconds"),
           "source": {"log": str(LOG), "minutes": minutes}, "template": TEMPLATE, "instructions": INSTRUCTIONS,
           "rule": f"free {PITCH} m lattice slots aligned to the civic centre, within {REACH} m of a building or a house",
           "maps": {}, "dropped": [], "steps": [], "summary": {}}
    houses: list = []
    dropped: dict = {}
    total = len(minutes)
    b = None
    for n, mnt in enumerate(minutes, 1):
        snap = json.loads(json.dumps(snaps[mnt]))
        snap["structures"] = [st for st in snap["structures"] if not is_house(st) and skey(st) not in dropped]
        m = Map(snap, hsize)
        hrects = [(k[0] * PITCH, k[1] * PITCH, PITCH / 2, PITCH / 2) for k in houses]
        keep = []
        for st in snap["structures"]:
            if any(Map.gap(m.rect(st), hr) < 0 for hr in hrects):
                dropped[skey(st)] = mnt
                run["dropped"].append({"minute": mnt, "tpl": st["tpl"], "x": st["x"], "z": st["z"], "a": st["a"],
                                       "w": st["w"], "d": st["d"], "done": st["done"]})
            else:
                keep.append(st)
        snap["structures"] = keep
        m = Map(snap, hsize)
        b = (PiecesBuilder if VARIANT["pieces"] else Builder)(m, goal, houses)
        if str(mnt) not in run["maps"]:
            run["maps"][str(mnt)] = map_payload(m)
        run.setdefault("map", map_payload(m))
        prompt, options = build(b, goal, houses, n, total)
        if not options:
            run["summary"]["error"] = f"q{n}: no free slot left"
            break
        byid = {o["id"]: o for o in options}
        t0 = time.time()
        if dry:
            r = {"ok": True, "choice": options[0]["id"],
                 "probabilities": {o["id"]: (1.0 if i == 0 else 0.0) for i, o in enumerate(options)}}
        else:
            r = cd.ask_retry(prompt, options, n)
        if not r.get("ok"):
            run["summary"]["error"] = f"q{n}: {r.get('error')}"
            run["steps"].append({"n": n, "minute": mnt, "prompt": prompt, "error": r.get("error")})
            break
        k = byid[r["choice"]]["slot"]
        houses.append(k)
        probs = r.get("probabilities") or {}
        top = sorted(probs, key=lambda i: -probs[i])[:5]
        run["steps"].append({
            "n": n, "minute": mnt, "prompt": prompt, "n_options": len(options),
            "slots": {o["id"]: slot_world(b, o["slot"]) for o in options},
            "top": [{"id": i, "p": probs[i], "text": byid[i]["text"]} for i in top if i in byid],
            "choice": r["choice"], "text": byid[r["choice"]]["text"], "p": probs.get(r["choice"]),
            "house": slot_world(b, k), "ms": r.get("ms") or round((time.time() - t0) * 1000)})
        run["summary"] = {**run_facts(b, houses), "dropped": len(run["dropped"])}
        save_real(run)
    # final facts on the last minute's map, with Petra's later structures that overlap Jev's houses dropped
    run["summary"] = {**run_facts(b, houses), "dropped": len(run["dropped"]),
                      **({"error": run["summary"]["error"]} if run["summary"].get("error") else {})}
    run["houses"] = [slot_world(b, k) for k in houses]
    save_real(run)
    s = run["summary"]
    kinds = {}
    for d in run["dropped"]:
        kinds[short(d["tpl"])] = kinds.get(short(d["tpl"]), 0) + 1
    print(f"run {run['id']}: {s['houses']} houses at minutes {minutes}, touching another {s['touching_another']}, "
          f"lines>=3 {s['lines_3plus']}, gap to CC min/mean {s['gap_cc_min']}/{s['gap_cc_mean']} m, next to field "
          f"{s['next_to_field']}, next to woods {s['next_to_woods']}, by side {s['by_side']}, dropped {kinds}"
          + (f", error: {s['error']}" if s.get("error") else ""))
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--civ", choices=["spart", "iber", "germ"], required=True)
    ap.add_argument("--minute", type=int, default=10)
    ap.add_argument("--houses", type=int, default=12)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--dry", action="store_true", help="no Jev: take the first option (nearest the CC)")
    ap.add_argument("--print", type=int, default=0, help="print the prompt + options of question N (dry houses before it)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--variant", choices=["real1", "real2", "pieces"], default="real1",
                    help="real2: lines say whether they run around or away from the civic centre")
    ap.add_argument("--goal-text", default=None, help="test a proposed civ line (the civ JSON is not touched)")
    ap.add_argument("--timeline", action="store_true",
                    help="real timeline: question k at the minute Petra's k-th house appears, that minute's map")
    args = ap.parse_args(argv)
    cd.VERBOSE = args.verbose
    VARIANT.update(name=args.variant, around=args.variant == "real2", pieces=args.variant == "pieces")
    if args.print:
        m = Map(load_snapshot(LOG, args.minute))
        goal = args.goal_text or civ_line(args.civ)
        b = Builder(m, goal)
        houses = []
        for n in range(1, args.print):
            houses.append(b.candidates(houses)[0])
        prompt, options = build(b, goal, houses, args.print, args.houses)
        print(prompt)
        print(f"--- {len(options)} options (sent as criteria only):")
        for o in options:
            print(f"- {o['id']}: {o['text']}")
        return 0
    for _ in range(args.runs):
        if args.timeline:
            timeline_run(args.civ, args.dry, args.goal_text)
        else:
            one_run(args.civ, args.minute, args.houses, args.dry, args.goal_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
