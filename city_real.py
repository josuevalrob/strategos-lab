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
import heapq
import json
import math
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import city_demo as cd

REPO0AD = Path("~/Projects/0AD").expanduser()
LOG = REPO0AD / ".claude/strategos/runs/solo/20261006-213701/engine.log"
CIVS = REPO0AD / "binaries/data/mods/strategos/simulation/data/strategos/civs"
DATA = Path(__file__).resolve().parent / "city_data"
FACT_WORDS = {k: v for k, v in json.loads((DATA / "fact_words.json").read_text()).items() if not k.startswith("_")}
LAYOUT = json.loads((DATA / "civ_layout.json").read_text())   # where a civ JSON keeps its town-layout line
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
        da = (s["a"] - self.a) % math.pi
        if min(da, math.pi - da) < 0.05:
            return (u, v, s["w"] / 2, s["d"] / 2)
        if abs(da - math.pi / 2) < 0.05:      # turned a quarter: width runs along the CC's facing
            return (u, v, s["d"] / 2, s["w"] / 2)
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
        return f"{metres(g)} from the civic centre" + ("" if VARIANT.get("no_far") else ", far from it")


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
        self.mentions = {k: any(w in self.goal for w in ws) for k, ws in FACT_WORDS.items()}

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
        pre = "does not stand apart: " if VARIANT.get("apart_both") else ""
        if side_by:
            return f"{pre}shoulder to shoulder with {num(side_by)} house{'s' if side_by > 1 else ''}"
        if any((k[0] + a, k[1] + b) in houses for a, b in ((1, 1), (1, -1), (-1, 1), (-1, -1))):
            return f"{pre}touches a house only at a corner"
        r = self.slots[k]
        g = min(Map.gap(r, self.slots[h]) for h in houses)
        if VARIANT.get("apart_plain"):     # no street word: "stands apart, 14 m from the nearest house"
            return f"stands apart, {metres(g)} from the nearest house"
        if g > 15 and VARIANT.get("apart_words"):
            return f"stands apart, more than one street ({metres(g)}) from the nearest house"
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
        if VARIANT.get("neither_words") and self.mentions["field"] and self.mentions["wood"] and self.fields and m.trees:
            gf = round(min(Map.gap(r, f) for f in self.fields))
            gw = round(min(m.point_gap(r, x, z) for x, z in m.trees))
            if gf > 6 and gw > 10:     # say the consequence once instead of two bare distances
                if VARIANT.get("far_bare"):         # no goal nouns at all for a slot far from both
                    out.append("on bare ground")
                elif not VARIANT.get("near_only"):     # near_only: a slot far from both says nothing about them
                    out.append(f"no field and no woods next to it (nearest field {metres(gf)}, nearest trees {metres(gw)})")
                return out + self._other_near(k, houses)
            if VARIANT.get("near_only"):
                if gf <= 6:
                    f = min(self.fields, key=lambda f: Map.gap(r, f))
                    own = not any(Map.gap(self.slots[h], f) <= 6 for h in houses)
                    out.append(f"next to a field ({metres(max(gf, 0))})" +
                               (", a field with no house beside it yet" if own else ", a field that already has a house beside it"))
                if gw <= 10:
                    out.append(f"at the edge of the woods ({metres(gw)} to the nearest tree)")
                return out + self._other_near(k, houses)
        if self.mentions["field"] and self.fields:
            g, f = min((round(Map.gap(r, f)), f) for f in self.fields)
            if g <= 6:
                own = not any(Map.gap(self.slots[h], f) <= 6 for h in houses)
                out.append(f"next to a field ({metres(max(g, 0))})" +
                           (", a field with no house beside it yet" if own else ", a field that already has a house beside it"))
            else:
                out.append(f"{metres(g)} from the nearest field")
        if self.mentions["wood"] and m.trees:
            g = round(min(m.point_gap(r, x, z) for x, z in m.trees))
            out.append(f"at the edge of the woods ({metres(g)} to the nearest tree)" if g <= 10
                       else f"{metres(g)} from the nearest trees")
        return out + self._other_near(k, houses)

    def _other_near(self, k, houses) -> list:
        m, r, out = self.m, self.slots[k], []
        if self.mentions["storehouse"] and self.stores:
            g = min(Map.gap(r, s) for s in self.stores)
            out.append(f"next to a storehouse ({metres(max(g, 0))})" if g <= 10 else f"{metres(g)} from the nearest storehouse")
        if self.mentions["open"]:
            if VARIANT.get("open_houses"):     # open ground = no house or (non-field) building near; fields/trees are fine
                things = [Map.gap(r, self.m.rect(s)) for s in self.m.structs if not s["tpl"].endswith("/field")] + \
                         [Map.gap(r, self.slots[h]) for h in houses]
                out.append("open ground around it (no house or building within 8 m)" if min(things) > 8
                           else "close to other houses or buildings")
            else:
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
        touch, near = self.touch(k, houses), self.near_facts(k, houses)
        if VARIANT.get("compose") and touch.startswith("stands apart"):
            # one phrase for two facts that hold together: "stands apart …, at the edge of the woods (…)"
            joint = [p for p in near if p.startswith(("next to a field", "at the edge of the woods"))]
            if joint:
                touch = touch + ", " + " and ".join(joint)
                near = [p for p in near if p not in joint]
        parts = [self.w.side(r[0], r[1])]
        if not VARIANT.get("gate_cc") or self.mentions.get("cc_gap"):
            parts.append(self.w.cc_gap(Map.gap(r, self.cc_rect)))
        parts.append(touch)
        parts += self.anchor(k, houses) + near
        if VARIANT.get("near_first"):      # the goal's own nouns (field, woods, storehouse …) lead the option
            lead = [p for p in parts if p.startswith(("next to a", "at the edge of the woods", "no field and no woods", "on bare ground"))]
            if VARIANT.get("compose") and touch.startswith("stands apart") and ", next to a" in touch or ", at the edge" in touch:
                lead = [touch] + lead
            parts = lead + [p for p in parts if p not in lead]
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
            pre = "does not stand apart: " if VARIANT.get("apart_both") and "house" in side_by else ""
            return pre + "shoulder to shoulder with " + kind_list(side_by)
        return super().touch(k, houses)

    def anchor(self, k, houses) -> list:
        ps = self.pieces(houses)
        r = self.slots[k]
        ls = self.lines(ps)
        out = []
        gate = VARIANT.get("gate_lines")
        for ax in ((0, 1) if not gate or self.mentions.get("lines") else ()):
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
        if len(band) >= 2 and (not gate or self.mentions.get("band")):
            before = self.faces([ps2[i]["rect"] for i in band if i != len(ps2) - 1])
            after = self.faces([ps2[i]["rect"] for i in band])
            if after:
                out.append(f"joins touching buildings that then line {self.face_list(after)} of the civic centre"
                           + (f" (before: {self.face_list(before)})" if before and before != after else ""))
        return out[:3]

    def state(self, houses) -> str:
        lines = self.map_lines() + [f"Houses built so far: {num(len(houses)) if houses else 'none yet'}."]
        ps = self.pieces(houses)
        gate = VARIANT.get("gate_lines")
        ls = sorted(self.lines(ps), key=lambda l: -len(l[1])) if not gate or self.mentions.get("lines") else []
        if ls:
            lines.append("Lines of buildings so far (houses, fields and Petra's other buildings): " +
                         "; ".join(f"a {self.pline_name(ax, idx, ps)}" for ax, idx in ls[:8]) + ".")
        bands = [c for c in self.comps(ps, lambda a, b: Map.gap(a, b) <= TOL) if len(c) >= 2] \
            if not gate or self.mentions.get("band") else []
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
    if VARIANT.get("shuffle"):      # Jev favours early options: list them in a fixed pseudo-random order, not nearest-first
        import random
        cands = sorted(cands)
        random.Random(1000 + n).shuffle(cands)
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


def civ_line(civ: str, line: str | None = None) -> str:
    """The civ's town-layout line from the named field (city_data/civ_layout.json), civ JSON first, then the
    lab-side override.  No per-civ code and no text[] index.  `line` names a lab-side override to read instead,
    city_data/civ_overrides/<civ>.<line>.json (a proposed line; the civ JSON is not touched)."""
    def get(d):
        for part in LAYOUT["field"].split("."):
            d = d.get(part) if isinstance(d, dict) else None
        return d
    if line:
        path = DATA / "civ_overrides" / f"{civ}.{line}.json"
        return get(json.loads(path.read_text())) or exit(f"{path}: no {LAYOUT['field']}")
    for path in (CIVS / f"{civ}.json", DATA / "civ_overrides" / f"{civ}.json"):
        if path.exists():
            line = get(json.loads(path.read_text()))
            if line:
                return line
    raise SystemExit(f"{civ}: no {LAYOUT['field']} in civs/{civ}.json or city_data/civ_overrides/{civ}.json")


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
           "maps": {}, "dropped": [], "steps": [], "summary": {}, "flags": dict(VARIANT)}
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


# == round 8+: the town -- every City Planner building Petra queued, on the real timeline ==========
TOWN_LOG = REPO0AD / ".claude/strategos/runs/solo/20261007-001734/engine.log"
TPL_DIR = REPO0AD / "binaries/data/mods/public/simulation/templates"
PLANNER = json.loads((DATA / "planner_classes.json").read_text())
RES_R = {"wood": TREE_R, "food.fruit": TREE_R, "stone": MINE_R, "metal": MINE_R}   # hunt (food.meat) walks away
MINE_WALL = 5.0      # what a mine itself blocks (units cannot cross it); buildings keep MINE_R clear
FLUSH = 1.0          # shoulder to shoulder: 1 m apart, still closed to units (v1's block gap)
HOLE_SLACK = 2.0     # a hole spot leaves at most this on each side (the judge closes gaps up to 2 m)
ORD = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh",
       "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth"]
TOWARD = {(0, 1): "right flank", (0, -1): "left flank", (1, 1): "front", (1, -1): "back"}   # (axis, sign) -> CC side
_TPL: dict = {}


def template_info(tpl: str) -> dict:
    """Identity classes, footprint and planner word of a template, following its parents (templates read only)."""
    if tpl not in _TPL:
        import re                      # regex, not ElementTree: this Python's expat does not load
        chain, name = [], tpl
        while name:
            text = re.sub(r"<!--.*?-->", "", (TPL_DIR / f"{name}.xml").read_text(), flags=re.S)
            chain.append(text)
            pm = re.search(r'<Entity[^>]*\bparent="([^"]+)"', text)
            name = pm.group(1) if pm else None
        toks, size = {"Classes": set(), "VisibleClasses": set()}, None
        for text in reversed(chain):
            ident = re.search(r"<Identity[^>]*>(.*?)</Identity>", text, re.S)
            for tag, attrs, body in re.findall(r"<(Classes|VisibleClasses)([^>]*)>([^<]*)</\1>", ident.group(1) if ident else ""):
                cls = toks[tag]
                if "replace" in attrs:
                    cls.clear()
                for t in body.split():
                    cls.discard(t[1:]) if t.startswith("-") else cls.add(t)
            obst = re.search(r"<Obstruction[^>]*>(.*?)</Obstruction>", text, re.S)
            st = re.search(r"<Static\b([^>]*)/?>", obst.group(1)) if obst else None
            if st:
                at = dict(re.findall(r'(\w+)="([^"]*)"', st.group(1)))
                size = (float(at["width"]), float(at["depth"]))
        classes = toks["Classes"] | toks["VisibleClasses"]
        pc = next((c for c in PLANNER["classes"] if c in classes), None)
        _TPL[tpl] = {"classes": sorted(classes), "planner": pc, "kind": PLANNER["words"][pc] if pc else short(tpl),
                     "w": size[0] if size else None, "d": size[1] if size else None}
    return _TPL[tpl]


def planner_struct(st) -> bool:
    return any(c in PLANNER["classes"] for c in st.get("cls") or [])


def queue_events(path: Path, until: int, player: int = 1) -> list:
    """Every City Planner building Petra queued up to minute `until`: one event per plan that entered a queue
    (a rise in that template's plan count between two queue lines), at that line's minute."""
    prev, out = {}, []
    for line in path.read_text(errors="replace").splitlines():
        i = line.find("[strategos] queue ")
        if i < 0:
            continue
        q = json.loads(line[i + len("[strategos] queue "):])
        if q.get("player") != player:
            continue
        if q["m"] > until:
            break
        cur = {}
        for qd in q["queues"].values():
            for p in qd.get("plans", []):
                if p["what"].startswith("structures/"):
                    cur[p["what"]] = cur.get(p["what"], 0) + p.get("n", 1)
        for tpl, c in cur.items():
            info = template_info(tpl)
            for _ in range(c - prev.get(tpl, 0)):
                if info["planner"]:
                    out.append({"minute": q["m"], "t": q["t"], "tpl": tpl, "kind": info["kind"], "cls": info["planner"],
                                "w": info["w"], "d": info["d"]})
        prev = cur
    return out


def overlap(a, b, ax) -> float:
    """Metres two rects share along axis ax (0 = u, 1 = v)."""
    return min(a[ax] + a[2 + ax], b[ax] + b[2 + ax]) - max(a[ax] - a[2 + ax], b[ax] - b[2 + ax])


class TownBuilder:
    """Spots, state and options for the next City Planner building, from what stands now.  Never sees a target.
    Ring pieces = the City Planner's own buildings; Petra's buildings (fields too) are obstacles, never ring pieces."""

    def __init__(self, m: Map, goal: str, placed: list, new: dict):
        self.m, self.w, self.goal, self.new = m, Words(m), goal.lower(), new
        self.cc_rect = m.rect(m.cc)
        self.anchors = [{"kind": short(s["tpl"]), "rect": m.rect(s)} for s in m.structs if s is not m.cc]
        self.placed = placed                 # [{"kind", "rect"}] the City Planner's buildings so far
        self.mentions = {k: any(w in self.goal for w in ws) for k, ws in FACT_WORDS.items()}

    # -- spots: flush against a face of what stands, or one street from it; any footprint -------------
    def shapes(self) -> list:
        hw, hd = self.new["w"] / 2, self.new["d"] / 2
        if abs(hw - hd) <= 0.5:          # near-square (a 7 x 8 tower): its bounding square, one shape
            return [(max(hw, hd), max(hw, hd))]
        return [(hw, hd), (hd, hw)]

    @staticmethod
    def around(s, hu, hv, g):
        su, sv, shu, shv = s
        for sign in (1, -1):
            cu = su + sign * (shu + g + hu)
            for cv in (sv - shv + hv, sv + shv - hv):      # lined up with either end of that face
                yield cu, cv
            cv = sv + sign * (shv + g + hv)
            for cu in (su - shu + hu, su + shu - hu):
                yield cu, cv

    def pair_spots(self, hu, hv):
        """Flush against a u-face of one City Planner building and a v-face of another at once (fills the corner
        where two rows meet); kept only when it touches both."""
        ps = [p["rect"] for p in self.placed]
        for a in ps:
            for b in ps:
                if a is b:
                    continue
                for su in (1, -1):
                    cu = a[0] + su * (a[2] + FLUSH + hu)
                    for sv in (1, -1):
                        cv = b[1] + sv * (b[3] + FLUSH + hv)
                        r = (cu, cv, hu, hv)
                        if Map.gap(r, a) <= TOL and Map.gap(r, b) <= TOL:
                            yield cu, cv

    @staticmethod
    def diagonal(s, hu, hv, g1, g2):
        """Off a corner of s: g1 from one face and g2 from the other (a ring's corner piece)."""
        su, sv, shu, shv = s
        for a in (1, -1):
            for b in (1, -1):
                yield su + a * (shu + g1 + hu), sv + b * (shv + g2 + hv)

    def possible(self, r) -> bool:
        m = self.m
        pts = [m.wld(r[0] + a, r[1] + b) for a in (-r[2], 0, r[2]) for b in (-r[3], 0, r[3])]
        if not all(m.in_terr(x, z) for x, z in pts):
            return False
        if Map.gap(r, self.cc_rect) < 0 or any(Map.gap(r, p["rect"]) < 0 for p in self.anchors + self.placed):
            return False
        return not any(m.point_gap(r, x, z) < RES_R[k] for k, x, z in m.res if k in RES_R)

    def holes(self, hu, hv):
        """Spots centred in a gap between two facing City Planner buildings that this building closes on its own
        (at most HOLE_SLACK m left on each side), lined up with either building's ends."""
        ps = [p["rect"] for p in self.placed]
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                for ax in (0, 1):
                    o, h = 1 - ax, (hu, hv)
                    lo, hi = (a, b) if a[ax] < b[ax] else (b, a)
                    gap = (hi[ax] - hi[2 + ax]) - (lo[ax] + lo[2 + ax])
                    if overlap(a, b, o) <= 0 or not 2 * h[ax] + 2 * FLUSH <= gap <= 2 * h[ax] + 2 * HOLE_SLACK:
                        continue
                    c = (lo[ax] + lo[2 + ax] + hi[ax] - hi[2 + ax]) / 2
                    for e in (lo, hi):
                        for oc in (e[o] - e[2 + o] + h[o], e[o] + e[2 + o] - h[o]):
                            yield (c, oc) if ax == 0 else (oc, c)

    def spots(self) -> list:
        srcs = [self.cc_rect] + [p["rect"] for p in self.anchors + self.placed]
        seen, out = set(), []
        for hu, hv in self.shapes():
            cands = [c for s in srcs for g in (FLUSH, STREET) for c in self.around(s, hu, hv, g)]
            if VARIANT.get("holes"):
                cands += list(self.holes(hu, hv))
            if VARIANT.get("diag"):
                cands += [c for s in srcs for g1 in (FLUSH, STREET) for g2 in (FLUSH, STREET)
                          for c in self.diagonal(s, hu, hv, g1, g2)]
            if VARIANT.get("pairs"):
                cands += list(self.pair_spots(hu, hv))
            for cu, cv in cands:
                r = (round(cu * 2) / 2, round(cv * 2) / 2, hu, hv)
                if r not in seen:
                    seen.add(r)
                    if self.possible(r):
                        out.append(r)
        out.sort(key=lambda r: (Map.gap(r, self.cc_rect), r))
        return out

    # -- geometry facts --------------------------------------------------------------------------------
    def radial(self, r):
        """(axis, sign): the CC side r looks at, by the larger of its two gaps to the civic centre."""
        c = self.cc_rect
        gu, gv = abs(r[0]) - r[2] - c[2], abs(r[1]) - r[3] - c[3]
        ax = 1 if gv >= gu else 0
        return ax, (1 if r[ax] > 0 else -1)

    def inward(self, r, pieces):
        """The nearest of `pieces` straight between r and the civic centre: (gap m, piece) or None."""
        ax, sg = self.radial(r)
        o, inner = 1 - ax, sg * r[ax] - r[2 + ax]
        best = None
        for p in pieces:
            pr = p["rect"]
            if pr is r or overlap(r, pr, o) <= 1 or sg * pr[ax] <= 0:
                continue
            outer = sg * pr[ax] + pr[2 + ax]
            if outer <= inner + 0.5 and (best is None or inner - outer < best[0]):
                best = (inner - outer, p)
        return best

    def layer(self, r, pieces, memo=None) -> int:
        memo = {} if memo is None else memo
        if r in memo:
            return memo[r]
        memo[r] = 1
        hit = self.inward(r, [p for p in pieces if p["rect"] != r])
        memo[r] = 1 if hit is None else 1 + self.layer(hit[1]["rect"], pieces, memo)
        return memo[r]

    def side_name(self, r) -> str:
        return self.w.side(r[0], r[1]).split(" (")[0]

    def face_word(self, r) -> str:
        """'front side' / 'left flank' ... or 'east corner' for a corner spot."""
        s = self.w.side(r[0], r[1])
        if s.startswith("off the "):
            return s[len("off the "):].split(" of the")[0]
        return s.split("(its ")[1].rstrip(")") + ("" if "flank" in s else " side")

    def rows(self, pieces) -> list:
        """Straight rows of 2+ City Planner buildings standing shoulder to shoulder: [(axis, [pieces in order])]."""
        out = []
        for ax in (0, 1):
            for comp in PiecesBuilder.comps(pieces, lambda a, b, ax=ax: inline(a, b, ax)):
                if len(comp) >= 2:
                    out.append((ax, sorted((pieces[i] for i in comp), key=lambda p: p["rect"][ax])))
        return out

    def row_name(self, ax, ps) -> str:
        rs = [p["rect"] for p in ps]
        mu, mv = sum(r[0] for r in rs) / len(rs), sum(r[1] for r in rs) / len(rs)
        return f"the row of {kind_list([p['kind'] for p in ps])} along the {self.face_word((mu, mv, 0, 0))}"

    def gap_words(self, g, what) -> str:
        sw = VARIANT.get("street_words", "plain")
        if g <= 1.5:
            return {"plain": f"touches {what}", "conseq": f"against {what}, with no street between them",
                    "wall": f"built against {what}", "goal": f"built against {what}"}[sw]
        if g < 8:
            return {"plain": f"{metres(g)} from {what}, narrower than a street",
                    "conseq": f"{metres(g)} from {what}: too narrow for a street",
                    "wall": f"{metres(g)} from {what}, a narrow lane", "goal": f"{metres(g)} from {what}, a narrow lane"}[sw]
        if g <= 12:
            return {"plain": f"one street ({metres(g)}) from {what}", "conseq": f"one street ({metres(g)}) from {what}",
                    "wall": f"a street ({metres(g)}) between it and {what}",
                    "goal": f"a street about 10 m wide between it and {what}"}[sw]
        return {"plain": f"{metres(g)} from {what}, more than one street",
                "conseq": f"{metres(g)} from {what}: wider than one street",
                "wall": f"{metres(g)} from {what}, more than a street", "goal": f"{metres(g)} from {what}, more than a street"}[sw]

    def ring_fact(self, r) -> str:
        """Which ring r would be in (counted from the civic centre over the City Planner's buildings) and the
        street between it and what stands straight inward."""
        hit = self.inward(r, self.placed)
        if hit is None:
            out = f"in the first ring, {self.gap_words(Map.gap(r, self.cc_rect), 'the civic centre')}"
        else:
            g, p = hit
            n = self.layer(p["rect"], self.placed)
            out = f"in the {ORD[n + 1]} ring, {self.gap_words(g, 'the ' + ORD[n] + ' ring')}"
        a = self.inward(r, self.anchors)
        if a and a[0] < (hit[0] if hit else Map.gap(r, self.cc_rect)) and a[0] < 8:
            out += f", {metres(a[0])} from a {a[1]['kind']} on the civic centre's side"
        return out

    def touch(self, r) -> str:
        if not self.placed:
            return "the first building of the city"
        side_by = [p["kind"] for p in self.placed if inline(r, p["rect"], 0) or inline(r, p["rect"], 1)]
        if side_by:
            return "shoulder to shoulder with " + kind_list(side_by)
        if any(Map.gap(r, p["rect"]) <= TOL for p in self.placed):
            return "touches a building only at a corner"
        g = min(Map.gap(r, p["rect"]) for p in self.placed)
        return f"stands apart, {metres(g)} from the nearest building of the city"

    def row_fact(self, r) -> list:
        ps, out = self.placed, []
        rows = self.rows(ps)
        row_of = {id(p): (ax, rp) for ax, rp in rows for p in rp}
        for ax in (0, 1):
            plus = [p for p in ps if inline(r, p["rect"], ax) and p["rect"][ax] > r[ax]]
            minus = [p for p in ps if inline(r, p["rect"], ax) and p["rect"][ax] < r[ax]]

            def name(p, ax=ax):
                rw = row_of.get(id(p))
                return self.row_name(*rw) if rw else f"the {p['kind']} on the {self.face_word(p['rect'])}"
            if plus and minus:
                out.append(f"closes the gap between {name(minus[0])} and {name(plus[0])}")
                continue
            for nb, sg in ((plus, -1), (minus, 1)):
                if not nb:
                    continue
                rw = row_of.get(id(nb[0]))
                toward = TOWARD[(ax, sg)]
                if rw and rw[0] == ax:
                    out.append(f"continues {self.row_name(*rw)} toward the {toward}")
                elif rw:
                    out.append(f"turns at the end of {self.row_name(*rw)}, toward the {toward}")
                else:
                    out.append(f"makes a row with {name(nb[0])}, toward the {toward}")
        return out[:2]

    def gate_fact(self, r) -> str:
        """The street straight out from the middle of a civic centre face: does r stand in it?"""
        c, half = self.cc_rect, STREET / 2
        for ax in (0, 1):
            o = 1 - ax
            if abs(r[o]) < r[2 + o] + half and abs(r[ax]) > c[2 + ax]:
                return f"blocks the way out to the {TOWARD[(ax, 1 if r[ax] > 0 else -1)]}"
        return ""

    def option_text(self, r) -> str:
        parts = [f"on the {self.face_word(r)} of the civic centre" if "corner" not in self.face_word(r)
                 else f"at the {self.face_word(r)} of the civic centre", self.ring_fact(r), self.touch(r)]
        parts += self.row_fact(r)
        g = self.gate_fact(r) if VARIANT.get("gate_corridor") else ""
        if g:
            parts.append(g)
        return f"a {self.new['kind']} " + "; ".join(parts)

    # -- state -----------------------------------------------------------------------------------------
    def state(self) -> str:
        w, ps = self.w, self.placed
        lines = [f"Our civic centre: its front faces {w.face['front']}, its flanks face {w.face['left flank']} "
                 f"and {w.face['right flank']}, its back faces {w.face['back']}."]
        if not ps:
            lines.append("Buildings of the city so far: none yet.")
            return "\n".join(lines)
        lines.append(f"Buildings of the city so far: {num(len(ps))}: {kind_list([p['kind'] for p in ps])}.")
        memo = {}
        by_layer = {}
        for p in ps:
            by_layer.setdefault(self.layer(p["rect"], ps, memo), []).append(p)
        rows = self.rows(ps)
        for n in sorted(by_layer):
            members = by_layer[n]
            ids = {id(p) for p in members}
            parts, done = [], set()
            for ax, rp in sorted(rows, key=lambda x: -len(x[1])):
                if all(id(p) in ids and id(p) not in done for p in rp):
                    done |= {id(p) for p in rp}
                    parts.append(f"{self.row_name(ax, rp)[4:]}, {self.dist_words(rp[0]['rect'], ps, n)}")
            for p in members:
                if id(p) not in done:
                    parts.append(f"a single {p['kind']} on the {self.face_word(p['rect'])}, {self.dist_words(p['rect'], ps, n)}")
            sides = []
            for p in members:
                f = self.face_word(p["rect"])
                if f not in sides:
                    sides.append(f)
            missing = [f for f in ("front side", "left flank", "back side", "right flank") if f not in sides]
            lines.append(f"The {ORD[n]} ring: " + "; ".join(parts) + ". " +
                         ("Nothing yet on the " + ", the ".join(missing) + "." if missing else "Buildings on all four sides."))
        return "\n".join(lines)

    def dist_words(self, r, ps, n) -> str:
        hit = self.inward(r, ps)
        if hit is None:
            return self.gap_words(Map.gap(r, self.cc_rect), "the civic centre")
        return self.gap_words(hit[0], f"the {ORD[n - 1]} ring")


class TownBuilder3(TownBuilder):
    """town3: rings by square distance from the civic centre (the goal's 'square rings'); each ring's openings in
    words (walked along a square loop through the ring); what a spot does to them."""

    def din(self, r) -> float:
        return max(0.0, Map.gap(r, self.cc_rect))

    def dout(self, r) -> float:
        c = self.cc_rect
        return max(abs(r[0]) + r[2] - c[2], abs(r[1]) + r[3] - c[3])

    def bands(self, ps) -> list:
        """[(core_in, core_out, members)] innermost first: every piece that starts before the outer face of the
        innermost one is in that ring; then the same over the rest."""
        left, out = list(ps), []
        while left:
            first = min(left, key=lambda p: self.din(p["rect"]))
            band = [p for p in left if self.din(p["rect"]) < self.dout(first["rect"]) - 1]
            ids = {id(p) for p in band}
            out.append((min(self.din(p["rect"]) for p in band), min(self.dout(p["rect"]) for p in band), band))
            left = [p for p in left if id(p) not in ids]
        return out

    def where_rings(self, r, bands):
        """(k, 'in' | 'inside' | 'outside'): in ring k, inside it (between ring k-1 or the CC and ring k), or outside
        the last ring k."""
        for k, (a, b, _) in enumerate(bands, 1):
            if self.din(r) < b - 1 and self.dout(r) > a + 1:
                return k, "in"
            if self.dout(r) <= a + 1:
                return k, "inside"
        return len(bands), "outside"

    @staticmethod
    def street_to(r, members) -> float:
        return min(Map.gap(r, m["rect"]) for m in members)

    def ring_fact(self, r) -> str:
        bands = self.bands(self.placed)
        cc = self.gap_words(self.din(r), "the civic centre")
        if not bands:
            return f"starts the first ring, {cc}"
        k, rel = self.where_rings(r, bands)
        if rel == "in":
            if k == 1:
                return f"in the first ring, {cc}"
            return f"in the {ORD[k]} ring, {self.gap_words(self.street_to(r, bands[k - 2][2]), 'the ' + ORD[k - 1] + ' ring')}"
        if rel == "inside":
            return (f"between the civic centre and the first ring, {cc}" if k == 1 else
                    f"between the {ORD[k - 1]} and the {ORD[k]} ring, "
                    f"{self.gap_words(self.street_to(r, bands[k - 2][2]), 'the ' + ORD[k - 1] + ' ring')}")
        out = f"starts the {ORD[k + 1]} ring, {self.gap_words(self.street_to(r, bands[k - 1][2]), 'the ' + ORD[k] + ' ring')}"
        if VARIANT.get("open_note"):
            ops = self.openings(bands[k - 1])
            if ops:
                out += f", while the {ORD[k]} ring is still open ({metres(sum(len(o) for o in ops))} of openings)"
        return out

    # -- openings: a square loop through the middle of a ring, sampled every metre ---------------------
    def loop(self, band):
        a, b, _ = band
        c, L = self.cc_rect, (a + b) / 2
        hu, hv = c[2] + L, c[3] + L
        pts = []      # (u, v, side, at a corner): one turn, front -> right flank -> back -> left flank
        for side, (u0, v0, u1, v1) in (("front side", (-hu, hv, hu, hv)), ("right flank", (hu, hv, hu, -hv)),
                                        ("back side", (hu, -hv, -hu, -hv)), ("left flank", (-hu, -hv, -hu, hv))):
            n = max(1, int(round(math.hypot(u1 - u0, v1 - v0))))
            for i in range(n):
                t = i / n
                pts.append((u0 + (u1 - u0) * t, v0 + (v1 - v0) * t, side, i == 0))
        return pts

    def corner_name(self, u, v) -> str:
        return self.w.side(math.copysign(100, u), math.copysign(100, v))[len("off the "):].split(" of the")[0]

    def openings(self, band, extra=None) -> list:
        """Runs of loop points no building of that ring covers (1 m gaps between buildings count as closed)."""
        pts = self.loop(band)
        rects = [m["rect"] for m in band[2]] + ([extra] if extra else [])
        g = FLUSH / 2 + 0.1          # 1 m gaps close; a run of k uncovered metres is a gap of about k + 1 m
        cov = [any(abs(u - r[0]) <= r[2] + g and abs(v - r[1]) <= r[3] + g for r in rects) for u, v, _, _ in pts]
        if not any(cov):
            return [list(range(len(pts)))]
        start = cov.index(True)
        runs, cur = [], []
        for j in range(len(pts)):
            i = (start + j) % len(pts)
            if not cov[i]:
                cur.append(i)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        return runs

    def run_where(self, band, run) -> str:
        pts = self.loop(band)
        sides = []
        for i in run:
            if pts[i][2] not in sides:
                sides.append(pts[i][2])
        corners = [self.corner_name(pts[i][0], pts[i][1]) for i in run if pts[i][3]]
        if len(run) >= len(pts) - 1:
            return "all around"
        if not corners:
            return f"on the {sides[0]}"
        if len(corners) == 1:
            return f"around the {corners[0]}"
        return "on the " + ", the ".join(sides[:-1]) + " and the " + sides[-1]

    def opening_fact(self, r) -> str:
        bands = self.bands(self.placed)
        if not bands:
            return ""
        k, rel = self.where_rings(r, bands)
        if rel != "in":
            return ""
        band = bands[k - 1]
        before, after = self.openings(band), self.openings(band, r)
        changed = [o for o in before if o not in after]
        if not changed:
            return ""
        o = changed[0]
        part = [x for x in after if set(x) <= set(o)]
        ring = f"of the {ORD[k]} ring"
        where = self.run_where(band, o)
        if not part:
            return f"closes the {metres(len(o))} opening {where} {ring}"
        if len(part) == 1:
            return f"narrows the opening {where} {ring} from {metres(len(o))} to {metres(len(part[0]))}"
        return f"splits the opening {where} {ring} into {' and '.join(metres(len(x)) for x in part)}"

    def row_fact(self, r) -> list:
        """Only along a ring: the same-ring neighbour it continues, toward which side."""
        bands = self.bands(self.placed)
        if not bands:
            return []
        k, rel = self.where_rings(r, bands)
        if rel != "in":
            return []
        mem = bands[k - 1][2]
        out = []
        for ax in (0, 1):
            plus = [p for p in mem if inline(r, p["rect"], ax) and p["rect"][ax] > r[ax]]
            minus = [p for p in mem if inline(r, p["rect"], ax) and p["rect"][ax] < r[ax]]
            if plus and minus:
                out.append(f"joins two buildings of the {ORD[k]} ring")
                continue
            for nb, sg in ((plus, -1), (minus, 1)):
                if nb:
                    out.append(f"continues the {ORD[k]} ring from the {nb[0]['kind']} on the {self.face_word(nb[0]['rect'])} "
                               f"toward the {TOWARD[(ax, sg)]}")
        return out[:1]

    def option_text(self, r) -> str:
        fw = self.face_word(r)
        parts = [f"at the {fw} of the civic centre" if "corner" in fw else f"on the {fw} of the civic centre",
                 self.ring_fact(r)]
        t = self.touch(r)
        if t:
            parts.append(t)
        o = self.opening_fact(r)
        if o:
            parts.append(o)
        parts += self.row_fact(r)
        return f"a {self.new['kind']} " + "; ".join(parts)

    def state(self) -> str:
        w, ps = self.w, self.placed
        lines = [f"Our civic centre: its front faces {w.face['front']}, its flanks face {w.face['left flank']} "
                 f"and {w.face['right flank']}, its back faces {w.face['back']}."]
        if not ps:
            lines.append("Buildings of the city so far: none yet.")
            return "\n".join(lines)
        lines.append(f"Buildings of the city so far: {num(len(ps))}: {kind_list([p['kind'] for p in ps])}.")
        bands = self.bands(ps)
        for k, band in enumerate(bands, 1):
            a, b, mem = band
            if k == 1:
                street = self.gap_words(a, "the civic centre")
            else:
                gs = sorted(self.street_to(p["rect"], bands[k - 2][2]) for p in mem)
                street = self.gap_words(gs[len(gs) // 2], f"the {ORD[k - 1]} ring")
            ops = self.openings(band)
            op = ("Openings: " + "; ".join(f"{metres(len(o))} {self.run_where(band, o)}" for o in
                                          sorted(ops, key=lambda o: -len(o))) + "." if ops else "It is closed all around.")
            lines.append(f"The {ORD[k]} ring ({street}): {num(len(mem))} building{'s' if len(mem) > 1 else ''}. {op}")
        return "\n".join(lines)


class TownBuilder4(TownBuilder3):
    """town4: a ring's street = the median inner distance of its buildings, so a building that starts inside it is
    'between' (not in) the ring; shoulder to shoulder only with buildings of the same ring; each option says the
    openings the ring is left with ('a gate one street wide' for a street-wide one)."""

    def bands(self, ps) -> list:
        out = []
        for _, _, band in super().bands(ps):
            ins = sorted(self.din(p["rect"]) for p in band)
            a = ins[len(ins) // 2]
            b = min(self.dout(p["rect"]) for p in band if self.din(p["rect"]) >= a - 2)
            out.append((a, b, band))
        return out

    def where_rings(self, r, bands):
        for k, (a, b, _) in enumerate(bands, 1):
            if self.din(r) < a - 2:
                return k, "inside"
            if self.din(r) < b - 1:
                return k, "in"
        return len(bands), "outside"

    def touch(self, r) -> str:
        if not self.placed:
            return "the first building of the city"
        bands = self.bands(self.placed)
        k, rel = self.where_rings(r, bands)
        same = bands[k - 1][2] if rel == "in" else []
        side_by = [p["kind"] for p in same if inline(r, p["rect"], 0) or inline(r, p["rect"], 1)]
        if side_by:
            return f"shoulder to shoulder with {kind_list(side_by)} of the {ORD[k]} ring"
        g = min(Map.gap(r, p["rect"]) for p in self.placed)
        if g <= TOL or (VARIANT.get("street_not_apart") and 8 <= g <= 12):
            return ""
        return f"stands apart, {metres(g)} from the nearest building of the city"

    def op_words(self, band, run) -> str:
        n, where = len(run) + 1, self.run_where(band, run)
        if 8 <= n <= 14:
            return f"a gate one street wide ({metres(n)}) {where}"
        if n < 8:
            return f"{metres(n)} {where}, narrower than a street"
        return f"{metres(n)} {where}"

    def opening_fact(self, r) -> str:
        bands = self.bands(self.placed)
        if not bands:
            return ""
        k, rel = self.where_rings(r, bands)
        if rel != "in":
            return ""
        band = bands[k - 1]
        before, after = self.openings(band), self.openings(band, r)
        if before == after:
            return ""
        if not after:
            return f"closes the {ORD[k]} ring all around: no opening left"
        return f"leaves the {ORD[k]} ring with these openings: " + ", ".join(
            self.op_words(band, o) for o in sorted(after, key=lambda o: -len(o)))

    def ring_fact(self, r) -> str:
        out = super().ring_fact(r)
        bands = self.bands(self.placed)
        if bands and out.startswith("starts the"):
            k = len(bands)
            wide = sum(len(o) for o in self.openings(bands[-1]) if len(o) > 14)
            if wide:
                out += f", while the {ORD[k]} ring is still open ({metres(wide)} of it unbuilt)"
        return out

    def state(self) -> str:
        w, ps = self.w, self.placed
        lines = [f"Our civic centre: its front faces {w.face['front']}, its flanks face {w.face['left flank']} "
                 f"and {w.face['right flank']}, its back faces {w.face['back']}."]
        if not ps:
            lines.append("Buildings of the city so far: none yet.")
            return "\n".join(lines)
        lines.append(f"Buildings of the city so far: {num(len(ps))}: {kind_list([p['kind'] for p in ps])}.")
        bands = self.bands(ps)
        for k, band in enumerate(bands, 1):
            a, b, mem = band
            if k == 1:
                street = self.gap_words(a, "the civic centre")
            else:
                gs = sorted(self.street_to(p["rect"], bands[k - 2][2]) for p in mem)
                street = self.gap_words(gs[len(gs) // 2], f"the {ORD[k - 1]} ring")
            ops = sorted(self.openings(band), key=lambda o: -len(o))
            op = ("Openings: " + "; ".join(self.op_words(band, o) for o in ops) + "." if ops
                  else "It is closed all around: no opening.")
            lines.append(f"The {ORD[k]} ring ({street}): {num(len(mem))} building{'s' if len(mem) > 1 else ''}. {op}")
        return "\n".join(lines)


WAY_G = 0.5          # town6: a building's wall, grown 0.5 m on 1 m cells (centres): 1 m gaps closed, 2 m a way out
MAX_WAYS = 6


class TownBuilder6(TownBuilder4):
    """town6: openings = the ways out of the city from the civic centre between ALL City Planner buildings (any ring),
    so a building that closes a gap from outside a ring counts too; the state lists them, each option says the ways
    out it leaves.  Ring words (square distance) as town4."""

    def ways_out(self, rects):
        """[(width m, angle deg in the CC frame: 0 right flank, 90 front)] or None (no wall yet); a last entry
        (None, None) when more ways out remain than MAX_WAYS."""
        if not rects:
            return None
        c = self.cc_rect
        half = int(max(max(abs(r[0]) + r[2], abs(r[1]) + r[3]) for r in rects)) + 12
        n = 2 * half + 1
        wall = bytearray(n * n)

        def paint(r, g):
            u0, u1 = max(0, math.ceil(r[0] - r[2] - g - 0.5) + half), min(n - 1, math.floor(r[0] + r[2] + g - 0.5) + half)
            v0, v1 = max(0, math.ceil(r[1] - r[3] - g - 0.5) + half), min(n - 1, math.floor(r[1] + r[3] + g - 0.5) + half)
            for i in range(u0, u1 + 1):
                wall[i * n + v0: i * n + v1 + 1] = b"\x01" * (v1 - v0 + 1)
        for r in rects:
            paint(r, WAY_G)
        big = 1 << 30
        clear = [0 if wall[i] else big for i in range(n * n)]
        dq = deque(i for i in range(n * n) if wall[i])
        while dq:
            i = dq.popleft()
            a, b = divmod(i, n)
            for j in (i + n if a + 1 < n else -1, i - n if a else -1, i + 1 if b + 1 < n else -1, i - 1 if b else -1):
                if j >= 0 and clear[j] > clear[i] + 1:
                    clear[j] = clear[i] + 1
                    dq.append(j)
        paint(c, 0.0)
        hu, hv = int(c[2]) + 2, int(c[3]) + 2
        starts = {(a + half) * n + (b + half) for a in range(-hu, hu + 1) for b in (-hv, hv)} | \
                 {(a + half) * n + (b + half) for a in (-hu, hu) for b in range(-hv, hv + 1)}
        ways = []
        for _ in range(MAX_WAYS):
            best, prev, heap = {}, {}, []
            for i in starts:
                if not wall[i]:
                    best[i], prev[i] = clear[i], -1
                    heapq.heappush(heap, (-clear[i], i))
            out = None
            while heap:
                nb, i = heapq.heappop(heap)
                if -nb < best.get(i, -1):
                    continue
                a, b = divmod(i, n)
                if a in (0, n - 1) or b in (0, n - 1):
                    out = i
                    break
                for j in (i + n if a + 1 < n else -1, i - n if a else -1, i + 1 if b + 1 < n else -1, i - 1 if b else -1):
                    if j >= 0 and not wall[j]:
                        bj = min(-nb, clear[j])
                        if bj > best.get(j, -1):
                            best[j], prev[j] = bj, i
                            heapq.heappush(heap, (-bj, j))
            if out is None:
                return ways
            bott, path, i = best[out], [], out
            while i != -1:
                path.append(i)
                i = prev[i]
            g = next(i for i in reversed(path) if clear[i] == bott)
            ga, gb = divmod(g, n)
            ways.append((2 * bott, math.degrees(math.atan2(gb - half, ga - half)) % 360))
            rad = bott + 1
            for a in range(max(0, ga - rad), min(n, ga + rad + 1)):
                wall[a * n + max(0, gb - rad): a * n + min(n, gb + rad + 1)] = b"\x01" * (min(n, gb + rad + 1) - max(0, gb - rad))
        ways.append((None, None))
        return ways

    def way_where(self, ang) -> str:
        for k in (45, 135, 225, 315):
            if abs((ang - k + 180) % 360 - 180) <= 15:
                rad = math.radians(k)
                return f"at the {self.corner_name(math.cos(rad), math.sin(rad))}"
        side = min(((0, "right flank"), (90, "front side"), (180, "left flank"), (270, "back side")),
                   key=lambda s: abs((ang - s[0] + 180) % 360 - 180))[1]
        return f"on the {side}"

    def way_words(self, ways) -> str:
        parts = []
        for wdt, ang in sorted(ways, key=lambda w: -(w[0] or 1e9)):
            if wdt is None:
                parts.append("more ways out on other sides")
            elif 8 <= wdt <= 14:
                parts.append(f"a gate one street wide ({metres(wdt)}) {self.way_where(ang)}")
            elif wdt < 8:
                parts.append(f"{metres(wdt)} {self.way_where(ang)}, narrower than a street")
            else:
                parts.append(f"{metres(wdt)} {self.way_where(ang)}")
        return ", ".join(parts)

    def _ways_before(self):
        if not hasattr(self, "_wb"):
            self._wb = self.ways_out([p["rect"] for p in self.placed])
        return self._wb

    def opening_fact(self, r) -> str:
        if not self.placed or min(Map.gap(r, p["rect"]) for p in self.placed) > 14:
            return ""
        before, after = self._ways_before(), self.ways_out([p["rect"] for p in self.placed] + [r])
        if after == before:
            return ""
        if not after:
            return "closes the city all around: no way out left"
        return "leaves the city with these ways out: " + self.way_words(after)

    def ring_fact(self, r) -> str:
        return TownBuilder3.ring_fact(self, r)

    def state(self) -> str:
        w, ps = self.w, self.placed
        lines = [f"Our civic centre: its front faces {w.face['front']}, its flanks face {w.face['left flank']} "
                 f"and {w.face['right flank']}, its back faces {w.face['back']}."]
        if not ps:
            lines.append("Buildings of the city so far: none yet.")
            return "\n".join(lines)
        lines.append(f"Buildings of the city so far: {num(len(ps))}: {kind_list([p['kind'] for p in ps])}.")
        bands = self.bands(ps)
        for k, (a, b, mem) in enumerate(bands, 1):
            if k == 1:
                street = self.gap_words(a, "the civic centre")
            else:
                gs = sorted(self.street_to(p["rect"], bands[k - 2][2]) for p in mem)
                street = self.gap_words(gs[len(gs) // 2], f"the {ORD[k - 1]} ring")
            sides = []
            for p in mem:
                f = self.face_word(p["rect"])
                if f not in sides:
                    sides.append(f)
            lines.append(f"The {ORD[k]} ring ({street}): {num(len(mem))} building{'s' if len(mem) > 1 else ''}, "
                         f"on the {', the '.join(sides)}.")
        ways = self._ways_before()
        lines.append("Ways out of the city: " + (self.way_words(ways) + "." if ways else "none: it is closed all around."))
        return "\n".join(lines)


class TownBuilder7(TownBuilder6):
    """town7: one main clause per option = ring relation + shoulder to shoulder + street, composed (lesson 16); the
    way straight out of the middle of each civic centre face (a street wide) when a spot stands in it; what the spot
    changes in the city's ways out (closes / narrows / opens), not the whole list."""

    def main_clause(self, r) -> str:
        bands = self.bands(self.placed)
        cc = self.gap_words(self.din(r), "the civic centre")
        if not bands:
            return f"starts the first ring, {cc}"
        k, rel = self.where_rings(r, bands)
        inner = cc if k == 1 else self.gap_words(self.street_to(r, bands[k - 2][2]), f"the {ORD[k - 1]} ring")
        if rel == "outside":
            out = f"starts the {ORD[k + 1]} ring, {self.gap_words(self.street_to(r, bands[k - 1][2]), 'the ' + ORD[k] + ' ring')}"
            wide = sum(len(o) + 1 for o in self.openings(bands[k - 1]) if len(o) + 1 > 14)
            return out + (f", while the {ORD[k]} ring is still open ({metres(wide)} of it unbuilt)" if wide else "")
        if rel == "inside":
            if k == 1 and VARIANT.get("inside_plain"):     # no ring words: it is in no ring
                return f"in no ring, {cc}"
            return f"between the civic centre and the first ring, {cc}" if k == 1 else \
                f"between the {ORD[k - 1]} and the {ORD[k]} ring, {inner}"
        mem = bands[k - 1][2]
        nbs = [(p, ax) for p in mem for ax in (0, 1) if inline(r, p["rect"], ax)]
        if nbs:
            sides = {(ax, 1 if r[ax] > p["rect"][ax] else -1) for p, ax in nbs}
            kinds = kind_list([p["kind"] for p, _ in nbs])
            if any((ax, -sg) in sides for ax, sg in sides):
                return f"closes a gap in the {ORD[k]} ring, shoulder to shoulder with {kinds}, {inner}"
            ax, sg = sorted(sides)[0]
            return f"continues the {ORD[k]} ring toward the {TOWARD[(ax, sg)]}, shoulder to shoulder with {kinds}, {inner}"
        p = min(mem, key=lambda q: Map.gap(r, q["rect"]))
        g = Map.gap(r, p["rect"])
        facing = any(overlap(r, p["rect"], 1 - ax) > 0.6 * min(2 * r[3 - ax], 2 * p["rect"][3 - ax]) for ax in (0, 1))
        if facing and 8 <= g <= 14:
            return f"in the {ORD[k]} ring, {inner}, leaving a gate one street wide ({metres(g)}) between it and the {p['kind']}"
        return f"in the {ORD[k]} ring, {inner}, {metres(g)} from its nearest building"

    def gate_street(self, r) -> str:
        c, half = self.cc_rect, STREET / 2
        for ax in (0, 1):
            o = 1 - ax
            if abs(r[o]) < r[2 + o] + half and abs(r[ax]) - r[2 + ax] > c[2 + ax] - 0.5:
                face = TOWARD[(ax, 1 if r[ax] > 0 else -1)]
                if VARIANT.get("gate_middle"):
                    return f"blocks the middle of the {face}{'' if 'flank' in face else ' side'}"
                return f"blocks the way straight out of the middle of the civic centre's {face}"
        return ""

    def opening_fact(self, r) -> str:
        if not self.placed or min(Map.gap(r, p["rect"]) for p in self.placed) > 14:
            return ""
        wb = self._ways_before()
        if wb is not None and not wb:
            return ""                                   # already closed all around
        before = [w for w in (wb or []) if w[0] is not None]
        after = self.ways_out([p["rect"] for p in self.placed] + [r])
        if after is not None and not after:
            return "closes the city all around: no way out left"
        after = [w for w in (after or []) if w[0] is not None]

        def near(a, b):
            return abs((a - b + 180) % 360 - 180) <= 25
        parts = []
        for w, a in sorted(before, key=lambda x: x[0]):
            m = [x for x in after if near(x[1], a)]
            if not m:
                parts.append(f"closes the {metres(w)} way out {self.way_where(a)}")
            elif max(x[0] for x in m) <= w - 2:
                nw = max(x[0] for x in m)
                parts.append(f"narrows the way out {self.way_where(a)} from {metres(w)} to " +
                             (f"a gate one street wide ({metres(nw)})" if 8 <= nw <= 14 else
                              f"{metres(nw)}, narrower than a street" if nw < 8 else metres(nw)))
        for w, a in after:
            if not any(near(a, b) for _, b in before):
                parts.append(f"opens a way out of {metres(w)} {self.way_where(a)}" + (", narrower than a street" if w < 8 else ""))
        return "; ".join(parts[:2])

    def option_text(self, r) -> str:
        fw = self.face_word(r)
        parts = [self.main_clause(r)]
        for extra in (self.gate_street(r), self.opening_fact(r)):
            if extra:
                parts.append(extra)
        where = f"at the {fw} of the civic centre" if "corner" in fw else f"on the {fw} of the civic centre"
        return f"a {self.new['kind']} {where}: " + "; ".join(parts)


class TownBuilder8(TownBuilder7):
    """town8: town7 without the middle-of-face fact (a goal phrase in it pulls toward blocking: round 10); a gate is
    named only where a spot leaves one (a street-wide gap to a building of its ring, with its side); way-out facts
    only for the two consequences that end a gate or open a slit."""

    def main_clause(self, r) -> str:
        out = super().main_clause(r)
        if ", leaving a gate one street wide (" in out:
            bands = self.bands(self.placed)
            k, _ = self.where_rings(r, bands)
            p = min(bands[k - 1][2], key=lambda q: Map.gap(r, q["rect"]))
            mid = ((r[0] + p["rect"][0]) / 2, (r[1] + p["rect"][1]) / 2, 0, 0)
            fw = self.face_word(mid)
            out = out.replace(") between it and the", f") {'at' if 'corner' in fw else 'on'} the {fw} between it and the")
        return out

    def gate_street(self, r) -> str:
        return ""

    def opening_fact(self, r) -> str:
        if not self.placed or min(Map.gap(r, p["rect"]) for p in self.placed) > 14:
            return ""
        wb = self._ways_before()
        if wb is not None and not wb:
            return ""
        after = self.ways_out([p["rect"] for p in self.placed] + [r])
        if after is not None and not after:
            return "closes the city all around: no way out left"
        before = [w for w in (wb or []) if w[0] is not None]
        shut = VARIANT.get("close_way")
        if VARIANT.get("close_way_off_ring"):   # only a spot in no ring (inside a ring's street or between rings)
            bands = self.bands(self.placed)
            shut = bool(bands) and self.where_rings(r, bands)[1] != "in"
        if shut:                            # a street-wide or wider way out that this spot shuts (any side)
            for w, a in sorted(before, key=lambda x: -x[0]):
                if w >= 8 and not any(v is not None and abs((a - b + 180) % 360 - 180) <= 25 for v, b in after or []):
                    return f"closes the {metres(w)} way out {self.way_where(a)}"
        for w, a in after or []:
            if w is not None and w < 8 and not any(abs((a - b + 180) % 360 - 180) <= 25 and v < 8 for v, b in before):
                return f"opens a way out of {metres(w)} {self.way_where(a)}, narrower than a street"
        return ""


SIDES4 = ("front side", "left flank", "right flank", "back side")


class TownBuilder9(TownBuilder8):
    """town9: what a spot does to each side and corner of its ring, in the goal's phrases: 'closes the back side of
    the first ring', 'closes the south corner of the first ring', 'leaves a gate one street wide (10 m) on the front
    side of the first ring'.  A side is open while an opening of at least 8 m touches it; a corner while any
    opening covers it.  Same rule for every side (the code never says which side should keep a gate)."""

    def side_state(self, band, runs):
        pts = self.loop(band)
        sides, corners = set(), set()
        for o in runs:
            w = len(o) + 1
            if w >= 8:
                sides |= {pts[i][2] for i in o if not pts[i][3]}
            corners |= {self.corner_name(pts[i][0], pts[i][1]) for i in o if pts[i][3]}
        return sides, corners

    def gate_left(self, band, runs, side):
        """Width of a street-wide opening (8-31 m) lying on `side`, or None."""
        pts = self.loop(band)
        for o in runs:
            w = len(o) + 1
            if 8 <= w <= 31 and all(pts[i][2] == side and not pts[i][3] for i in o):
                return w
        return None

    def side_fact(self, r) -> str:
        bands = self.bands(self.placed)
        if not bands:
            return ""
        k, rel = self.where_rings(r, bands)
        if rel != "in":
            return ""
        band = bands[k - 1]
        before, after = self.openings(band), self.openings(band, r)
        if before == after:
            return ""
        sb, cb = self.side_state(band, before)
        sa, ca = self.side_state(band, after)
        ring = f"of the {ORD[k]} ring"
        parts = [f"closes the {s} {ring}" for s in SIDES4 if s in sb and s not in sa]
        parts += [f"closes the {c} {ring}" for c in sorted(cb - ca)]
        fw = self.face_word(r)
        if fw in SIDES4 and fw in sa:
            g = self.gate_left(band, after, fw)
            if g and g != self.gate_left(band, before, fw):
                parts.append(f"leaves a gate one street wide ({metres(g)}) on the {fw} {ring}")
        if VARIANT.get("gate_early") and not any("leaves a gate" in x for x in parts):
            m = TownBuilder7.main_clause(self, r)      # a street-wide gap to the next building of its ring = a gate
            if ", leaving a gate one street wide (" in m:
                g = m.split(", leaving a gate one street wide (")[1].split(")")[0]
                p = min(band[2], key=lambda q: Map.gap(r, q["rect"]))
                side = self.face_word(((r[0] + p["rect"][0]) / 2, (r[1] + p["rect"][1]) / 2, 0, 0))
                parts.append(f"leaves a gate one street wide ({g}) {'at' if 'corner' in side else 'on'} the {side} {ring}")
        return "; ".join(parts[:2])

    def main_clause(self, r) -> str:
        out = TownBuilder7.main_clause(self, r)
        if ", leaving a gate one street wide (" in out:      # the side fact says it, in the same words for every side
            out = out.split(", leaving a gate one street wide (")[0]
            if VARIANT.get("gate_cont"):                      # one clause: the ring goes on across the gate
                bands = self.bands(self.placed)
                k, _ = self.where_rings(r, bands)
                p = min(bands[k - 1][2], key=lambda q: Map.gap(r, q["rect"]))
                ax = 0 if abs(r[0] - p["rect"][0]) - r[2] - p["rect"][2] > abs(r[1] - p["rect"][1]) - r[3] - p["rect"][3] else 1
                sg = 1 if r[ax] > p["rect"][ax] else -1
                mid = ((r[0] + p["rect"][0]) / 2, (r[1] + p["rect"][1]) / 2, 0, 0)
                fw = self.face_word(mid)
                inner = out.split(f"in the {ORD[k]} ring, ", 1)[1]
                out = (f"continues the {ORD[k]} ring toward the {TOWARD[(ax, sg)]} across a gate one street wide "
                       f"({metres(Map.gap(r, p['rect']))}) {'at' if 'corner' in fw else 'on'} the {fw}, {inner}")
        return out

    def option_text(self, r) -> str:
        fw = self.face_word(r)
        parts = [self.main_clause(r)]
        for extra in (self.side_fact(r), self.opening_fact(r)):
            if extra:
                parts.append(extra)
        where = f"at the {fw} of the civic centre" if "corner" in fw else f"on the {fw} of the civic centre"
        return f"a {self.new['kind']} {where}: " + "; ".join(parts)

    def state(self) -> str:
        w, ps = self.w, self.placed
        lines = [f"Our civic centre: its front faces {w.face['front']}, its flanks face {w.face['left flank']} "
                 f"and {w.face['right flank']}, its back faces {w.face['back']}."]
        if not ps:
            lines.append("Buildings of the city so far: none yet.")
            return "\n".join(lines)
        lines.append(f"Buildings of the city so far: {num(len(ps))}: {kind_list([p['kind'] for p in ps])}.")
        bands = self.bands(ps)
        for k, band in enumerate(bands, 1):
            a, b, mem = band
            street = self.gap_words(a, "the civic centre") if k == 1 else self.gap_words(
                sorted(self.street_to(p["rect"], bands[k - 2][2]) for p in mem)[len(mem) // 2], f"the {ORD[k - 1]} ring")
            runs = self.openings(band)
            so, co = self.side_state(band, runs)
            gates = [f"a gate one street wide ({metres(g)}) on the {s}" for s in SIDES4
                     for g in [self.gate_left(band, runs, s)] if g]
            closed = [s for s in SIDES4 if s not in so] + sorted(set(self.corner_name(u, v) for u, v in
                                                                    ((1, 1), (1, -1), (-1, 1), (-1, -1))) - co)
            opened = [s for s in SIDES4 if s in so and not self.gate_left(band, runs, s)] + sorted(co)
            lines.append(f"The {ORD[k]} ring ({street}): {num(len(mem))} building{'s' if len(mem) > 1 else ''}. "
                         + (f"Closed: the {', the '.join(closed)}. " if closed else "")
                         + (f"Gates: {', '.join(gates)}. " if gates else "")
                         + (f"Still open: the {', the '.join(opened)}." if opened else ""))
        return "\n".join(lines)


CORNER_DEG = 15      # a loop point within this of a diagonal (seen from the CC) is at a corner, as the judge counts


class TownBuilder12(TownBuilder9):
    """town12 (round 14): corners.  Why the corner gap: a row grows by 15 m steps from wherever it started (often a
    civic centre face end), so where it reaches the other side's row a strip of 2-13 m is left that no house fits.
    Spots added: in one row's line (lined up with its faces), flush against the perpendicular row of the same ring,
    so the corner is closed first and the leftover moves along the side; holes (round 8) fit a tower in a 10-12 m
    strip.  Corners are the loop points within 15 degrees of a diagonal (an opening near a corner is a corner
    opening, as the judge counts it), and the fact names the two rows: 'closes the corner of the first ring where
    the front row meets the left-flank row'."""

    def loop(self, band):
        out = []
        for u, v, side, _ in super().loop(band):
            ang = math.degrees(math.atan2(v, u)) % 360
            out.append((u, v, side, any(abs((ang - k + 180) % 360 - 180) <= CORNER_DEG for k in (45, 135, 225, 315))))
        return out

    @staticmethod
    def corner_key(u, v):
        return (1 if u > 0 else -1, 1 if v > 0 else -1)

    @staticmethod
    def corner_words(key) -> str:
        su, sv = key
        return f"where the {'front' if sv > 0 else 'back'} row meets the {'right' if su > 0 else 'left'}-flank row"

    def side_state(self, band, runs):
        pts = self.loop(band)
        sides, corners = set(), set()
        for o in runs:
            if len(o) + 1 >= 8:
                sides |= {pts[i][2] for i in o if not pts[i][3]}
            corners |= {self.corner_key(pts[i][0], pts[i][1]) for i in o if pts[i][3]}
        return sides, corners

    def cross_spots(self, hu, hv):
        """In the line of one ring building's row, flush against a building of the perpendicular row of the same
        ring (the corner where the two rows meet)."""
        for _, _, mem in self.bands(self.placed):
            for a in mem:
                ra, axa = a["rect"], self.radial(a["rect"])[0]
                for b in mem:
                    rb, axb = b["rect"], self.radial(b["rect"])[0]
                    if a is b or axa == axb:
                        continue
                    # a's row runs along axis 1 - axa (its line = a's extent across axa); b's row runs along 1 - axb = axa
                    h = (hu, hv)
                    line = (ra[axa] - ra[2 + axa] + h[axa], ra[axa] + ra[2 + axa] - h[axa])
                    toward = 1 if ra[1 - axa] > rb[1 - axa] else -1
                    along = rb[1 - axa] + toward * (rb[3 - axa] + FLUSH + h[1 - axa])
                    for c in line:
                        cu, cv = (c, along) if axa == 0 else (along, c)
                        r = (cu, cv, hu, hv)
                        if Map.gap(r, rb) <= TOL:
                            yield cu, cv

    def spots(self) -> list:
        out = super().spots()
        if not VARIANT.get("cross"):
            return out
        seen = set(out)
        for hu, hv in self.shapes():
            for cu, cv in self.cross_spots(hu, hv):
                r = (round(cu * 2) / 2, round(cv * 2) / 2, hu, hv)
                if r not in seen:
                    seen.add(r)
                    if self.possible(r):
                        out.append(r)
        out.sort(key=lambda r: (Map.gap(r, self.cc_rect), r))
        return out

    def along(self, r) -> str:
        """Where along its side a spot faces the civic centre: its middle, one half, or past one end."""
        fw = self.face_word(r)
        if fw not in SIDES4:
            return ""
        o = 0 if fw in ("front side", "back side") else 1
        t, half = r[o], self.cc_rect[2 + o]
        d = ("right" if t > 0 else "left") if o == 0 else ("front" if t > 0 else "back")
        if VARIANT.get("along_compass"):    # the direction by compass: no goal noun (front, flank) in it
            e = self.m.eu if o == 0 else self.m.ev
            d = compass(e[0] * (1 if t > 0 else -1), e[1] * (1 if t > 0 else -1))
        face = fw.replace(" side", "")
        if abs(t) <= 4:
            return f"facing the middle of the civic centre's {face}"
        if abs(t) <= half:
            return f"facing the {d} half of the civic centre's {face}"
        return f"past the {d} end of the civic centre's {face}"

    def option_text(self, r) -> str:
        if not VARIANT.get("along"):
            return super().option_text(r)
        fw = self.face_word(r)
        main = self.main_clause(r)
        parts = [main]
        for extra in (self.side_fact(r), self.opening_fact(r)):
            if extra:
                parts.append(extra)
        say_along = not VARIANT.get("along_ring_only") or main.startswith(("in the ", "continues the ", "closes a gap")) or \
            (hasattr(self, "beside_gate") and self.beside_gate(r))
        where = f"at the {fw} of the civic centre" if "corner" in fw else \
            f"on the {fw} of the civic centre" + (f", {self.along(r)}" if say_along else "")
        return f"a {self.new['kind']} {where}: " + "; ".join(parts)

    def side_fact(self, r) -> str:
        bands = self.bands(self.placed)
        if not bands:
            return ""
        k, rel = self.where_rings(r, bands)
        if rel != "in":
            return ""
        band = bands[k - 1]
        before, after = self.openings(band), self.openings(band, r)
        if before == after:
            return ""
        sb, cb = self.side_state(band, before)
        sa, ca = self.side_state(band, after)
        ring = f"of the {ORD[k]} ring"
        parts = [f"closes the corner {ring} {self.corner_words(c)}" for c in sorted(cb - ca)]
        if not VARIANT.get("no_side_close"):
            parts += [f"closes the {s} {ring}" for s in SIDES4 if s in sb and s not in sa]
        fw = self.face_word(r)
        if fw in SIDES4 and fw in sa:
            g = self.gate_left(band, after, fw)
            if g and g != self.gate_left(band, before, fw):
                parts.append(f"leaves a gate one street wide ({metres(g)}) on the {fw} {ring}")
        if VARIANT.get("gate_early") and not any("leaves a gate" in x for x in parts):
            m = TownBuilder7.main_clause(self, r)
            if ", leaving a gate one street wide (" in m:
                g = m.split(", leaving a gate one street wide (")[1].split(")")[0]
                p = min(band[2], key=lambda q: Map.gap(r, q["rect"]))
                side = self.face_word(((r[0] + p["rect"][0]) / 2, (r[1] + p["rect"][1]) / 2, 0, 0))
                parts.append(f"leaves a gate one street wide ({g}) {'at' if 'corner' in side else 'on'} the {side} {ring}")
        if VARIANT.get("gate_count"):          # the same words for every side: how many gates that side then has
            for i, x in enumerate(parts):
                if x.startswith("leaves a gate one street wide (") and " on the " in x:
                    side = x.split(" on the ")[1].replace(f" {ring}", "")
                    n = self.gate_gaps(band, r, side)
                    if n >= 2 or VARIANT.get("gate_first"):
                        parts[i] = x.replace("leaves a gate", f"leaves a {ORD[max(n, 1)]} gate" if n < len(ORD) else "leaves another gate")
        return "; ".join(parts[:2])

    def gate_gaps(self, band, r, side) -> int:
        """Street-wide gaps (8-31 m) between buildings of this ring on that side, once r stands."""
        rects = [m["rect"] for m in band[2]] + ([r] if r is not None else [])
        ax = 1 if side in ("front side", "back side") else 0      # the side's row runs across the other axis
        o, sg = 1 - ax, (1 if side in ("front side", "right flank") else -1)
        row = sorted((q for q in rects if self.face_word(q) == side), key=lambda q: q[o])
        n = 0
        for a, b in zip(row, row[1:]):
            g = (b[o] - b[2 + o]) - (a[o] + a[2 + o])
            if 8 <= g <= 31 and overlap(a, b, ax) > 0:
                n += 1
        return n

    def state(self) -> str:
        w, ps = self.w, self.placed
        lines = [f"Our civic centre: its front faces {w.face['front']}, its flanks face {w.face['left flank']} "
                 f"and {w.face['right flank']}, its back faces {w.face['back']}."]
        if not ps:
            lines.append("Buildings of the city so far: none yet.")
            return "\n".join(lines)
        lines.append(f"Buildings of the city so far: {num(len(ps))}: {kind_list([p['kind'] for p in ps])}.")
        bands = self.bands(ps)
        allc = [(-1, 1), (1, 1), (1, -1), (-1, -1)]
        for k, band in enumerate(bands, 1):
            a, b, mem = band
            street = self.gap_words(a, "the civic centre") if k == 1 else self.gap_words(
                sorted(self.street_to(p["rect"], bands[k - 2][2]) for p in mem)[len(mem) // 2], f"the {ORD[k - 1]} ring")
            runs = self.openings(band)
            so, co = self.side_state(band, runs)
            gates = [f"a gate one street wide ({metres(g)}) on the {s}" for s in SIDES4
                     for g in [self.gate_left(band, runs, s)] if g]
            closed = [f"the {s}" for s in SIDES4 if s not in so] + [f"the corner {self.corner_words(c)}" for c in allc if c not in co]
            opened = [f"the {s}" for s in SIDES4 if s in so and not self.gate_left(band, runs, s)] + \
                     [f"the corner {self.corner_words(c)}" for c in allc if c in co]
            tally = ""
            if VARIANT.get("gate_tally"):      # every side, the same words: how many gates it has so far
                cnt = {sd: self.gate_gaps(band, None, sd) for sd in SIDES4}
                tally = "Gates so far: " + ", ".join(
                    f"{['none', 'one', 'two', 'three', 'four'][min(cnt[sd], 4)]} on the {sd}" for sd in SIDES4) + ". "
            lines.append(f"The {ORD[k]} ring ({street}): {num(len(mem))} building{'s' if len(mem) > 1 else ''}. " + tally
                         + (f"Closed: {', '.join(closed)}. " if closed else "")
                         + (f"Gates: {', '.join(gates)}. " if gates and not tally else "")
                         + (f"Still open: {', '.join(opened)}." if opened else ""))
        return "\n".join(lines)


SIDE_AX = {"front side": (1, 1), "back side": (1, -1), "right flank": (0, 1), "left flank": (0, -1)}
GATE_SIDES = ("front side", "left flank", "right flank", "back side")
DEPTH_MAX = 20       # deepest planned building but the arsenal: a ring reaches this far out from its inner face
RES_NAME = {"stone": "the stone mine", "metal": "the metal mine", "food.fruit": "berry bushes", "wood": "trees"}
GATE_TEMPLATE = """Role: City planner.
Goal: {goal}
Map: a real map seen from above. A street is about {street} m wide.
{state}
The {ring} ring has just started: {first}.
Question: where is the gate on the {side} of the {ring} ring?"""


class TownBuilder15(TownBuilder12):
    """town15 (round 21): a gate question per side when a ring starts.  Options = positions along that side (5 m
    steps, the whole gate inside the side, not at a corner), in compass words from the side's middle, each with what
    stands in it and beyond it; plus 'no gate'.  A picked gate is a street-wide strip from the civic centre's face out
    through the ring and one street beyond; a building there would block it, so such spots are impossible (code
    removes them like any other impossible spot).  Gates of an inner ring keep the strip free, so an outer ring's
    gate can only be where it lines up.  Same four questions for every civ."""

    def possible(self, r) -> bool:
        if any(Map.gap(r, g) < 0 for g in getattr(self, "gate_rects", [])):
            return False
        return super().possible(r)

    def gate_edge_spots(self, hu, hv):
        """Against either edge of a picked gate's strip, inner face on the ring's inner face: the ring goes on on
        both sides of its gate."""
        c = self.cc_rect
        for g in getattr(self, "gate_list", []):
            if g["rect"] is None:
                continue
            ax, sg = SIDE_AX[g["side"]]
            o, h = 1 - ax, (hu, hv)
            radial = sg * (c[2 + ax] + g["a"] + h[ax])
            for side in (1, -1):
                along = g["t"] + side * (STREET / 2 + h[o])
                yield (along, radial) if ax == 1 else (radial, along)

    def nogate_spots(self, hu, hv):
        """A side answered 'no gate': the middle of that side (with nogate_all: every position the gate question
        offered there, 5 m apart), inner face on the ring's inner face."""
        c = self.cc_rect
        for g in getattr(self, "gate_list", []):
            if g["rect"] is not None:
                continue
            ax, sg = SIDE_AX[g["side"]]
            h = (hu, hv)
            radial = sg * (c[2 + ax] + g["a"] + h[ax])
            lim = (c[3 - ax] + g["a"] + 7) * math.tan(math.radians(30)) - STREET / 2
            ts = [5 * i for i in range(-int(lim // 5), int(lim // 5) + 1)] if VARIANT.get("nogate_all") else [0]
            for t in ts:
                yield (float(t), radial) if ax == 1 else (radial, float(t))

    def mine_sources(self) -> list:
        """Stone and metal mines near the city as squares of their clearance (terrain: a building may stand
        against that edge, not in it)."""
        m, out = self.m, []
        for k, x, z in m.res:
            if k in ("stone", "metal"):
                u, v = m.loc(x, z)
                if max(abs(u), abs(v)) < 90:
                    out.append((k, (u, v, MINE_R, MINE_R)))
        return out

    def spots(self) -> list:
        out = super().spots()
        if not VARIANT.get("gate_edges"):
            return out
        seen = set(out)
        extra = []
        for hu, hv in self.shapes():
            cands = list(self.gate_edge_spots(hu, hv))
            if VARIANT.get("nogate_mid"):
                cands += list(self.nogate_spots(hu, hv))
            if VARIANT.get("mine_edges"):
                cands += [cand for _, sq in self.mine_sources() for cand in self.around(sq, hu, hv, 0.0)]
            extra += [(cu, cv, hu, hv) for cu, cv in cands]
        for cu, cv, hu, hv in extra:
            r = (round(cu * 2) / 2, round(cv * 2) / 2, hu, hv)
            if r not in seen:
                seen.add(r)
                if self.possible(r):
                    out.append(r)
        out.sort(key=lambda r: (Map.gap(r, self.cc_rect), r))
        return out

    def side_fact(self, r) -> str:
        out = super().side_fact(r)
        if not VARIANT.get("upto_gate"):
            return out
        bands = self.bands(self.placed)
        if not bands:
            return out
        k, rel = self.where_rings(r, bands)
        fw = self.face_word(r)
        gate = next((g for g in getattr(self, "gate_list", []) if g["ring"] == k and g["side"] == fw and g["rect"] is not None), None)
        if rel != "in" or gate is None:
            return out
        band = bands[k - 1]
        o = 1 - SIDE_AX[fw][0]

        def up_to_gate(runs) -> bool:
            pts = self.loop(band)
            mine = [ru for ru in runs if any(pts[i][2] == fw and not pts[i][3] for i in ru)]
            return len(mine) == 1 and len(mine[0]) + 1 <= 31 and \
                any(abs((pts[i][0], pts[i][1])[o] - gate["t"]) <= STREET / 2 for i in mine[0])
        if up_to_gate(self.openings(band, r)) and not up_to_gate(self.openings(band)):
            fact = f"closes the {fw} of the {ORD[k]} ring up to its gate"
            return fact + ("; " + out if out else "")
        return out

    def beside_gate(self, r) -> str:
        for g in getattr(self, "gate_list", []):
            if g["rect"] is not None and -0.5 <= Map.gap(r, g["rect"]) <= 1.5:
                return f"beside the gate on the {g['side']} of the {ORD[g['ring']]} ring"
        return ""

    def option_text(self, r) -> str:
        t = super().option_text(r)
        extra = [self.beside_gate(r) if VARIANT.get("gate_edges") else ""]
        if VARIANT.get("nogate_mid"):
            for g in getattr(self, "gate_list", []):
                if g["rect"] is None and self.face_word(r) == g["side"]:
                    ax, _ = SIDE_AX[g["side"]]
                    on_mark = abs(r[1 - ax]) < 0.5 or (VARIANT.get("nogate_all") and abs(r[1 - ax] / 5 - round(r[1 - ax] / 5)) < 0.1)
                    if on_mark and abs(self.din(r) - g["a"]) < 1:
                        if VARIANT.get("nogate_lead"):      # the consequence first, in the goal's words
                            head, rest = t.split(": ", 1)
                            pos = "the middle of the" if abs(r[1 - ax]) < 0.5 else "part of the"
                            t = f"{head}: closes {pos} {g['side']} of the {ORD[g['ring']]} ring, which has no gate; {rest}"
                        else:
                            extra.append(f"in the middle of the {g['side']} of the {ORD[g['ring']]} ring, which has no gate")
        if VARIANT.get("mine_edges"):
            for k, sq in self.mine_sources():
                if -0.5 <= Map.gap(r, sq) <= 1.5:
                    extra.append(f"against the edge of {RES_NAME[k]}")
                    break
        extra = [e for e in extra if e]
        return t + ("; " + "; ".join(extra) if extra else "")

    def where_phrase(self, r) -> str:
        fw = self.face_word(r)
        return f"at the {fw} of the civic centre" if "corner" in fw else f"on the {fw} of the civic centre, {self.along(r)}"

    def gate_rect(self, side, a, t, r0=None, r1=None):
        ax, sg = SIDE_AX[side]
        c = self.cc_rect
        r0 = c[2 + ax] if r0 is None else r0
        r1 = c[2 + ax] + a + DEPTH_MAX + STREET if r1 is None else r1
        mid, half = sg * (r0 + r1) / 2, (r1 - r0) / 2
        return (t, mid, STREET / 2, half) if ax == 1 else (mid, t, half, STREET / 2)

    def gate_where(self, side, t) -> str:
        if t == 0:
            return f"in the middle of the {side}"
        ax, _ = SIDE_AX[side]
        e = self.m.eu if ax == 1 else self.m.ev
        d = compass(e[0] * (1 if t > 0 else -1), e[1] * (1 if t > 0 else -1))
        return f"{abs(t)} m {d} of the middle of the {side}"

    def gate_words(self, side, k, a, t) -> str:
        c, m = self.cc_rect, self.m
        ax, sg = SIDE_AX[side]
        inner, outer = c[2 + ax] + a, c[2 + ax] + a + DEPTH_MAX
        within = self.gate_rect(side, a, t, inner, outer)
        beyond = self.gate_rect(side, a, t, outer, outer + 50)
        beyond = (beyond[0], beyond[1], beyond[2] + 2, beyond[3]) if ax == 1 else (beyond[0], beyond[1], beyond[2], beyond[3] + 2)
        res_in, res_out = [], []
        for kind, x, z in m.res:
            if kind not in RES_R:
                continue
            if m.point_gap(within, x, z) < RES_R[kind] and RES_NAME[kind] not in res_in:
                res_in.append(RES_NAME[kind])
            elif m.point_gap(beyond, x, z) < RES_R[kind] and RES_NAME[kind] not in res_out:
                res_out.append(RES_NAME[kind])
        out_b = [p["kind"] for p in self.anchors if Map.gap(beyond, p["rect"]) < 0]
        parts = [f"{' and '.join(res_in)} {'stands' if len(res_in) == 1 and 'bushes' not in res_in[0] and res_in[0] != 'trees' else 'stand'} in it"] if res_in else []
        lead = ([kind_list(out_b)] if out_b else []) + res_out
        parts.append("its street leads out to " + (", ".join(lead) if lead else "open ground"))
        if m.enemy:
            ex, ez = m.enemy[0] - m.cc["x"], m.enemy[1] - m.cc["z"]
            eu, ev = ex * m.eu[0] + ez * m.eu[1], ex * m.ev[0] + ez * m.ev[1]
            if (sg * (ev if ax == 1 else eu)) > 0.7 * math.hypot(eu, ev):
                parts.append("toward the enemy")
        return ", ".join(parts)

    def gate_options(self, k, side, band) -> list:
        ax, _ = SIDE_AX[side]
        o, a, c = 1 - ax, band[0], self.cc_rect
        lim = (c[2 + o] + a + 7) * math.tan(math.radians(30)) - STREET / 2
        out = []
        r0 = None if k == 1 or not VARIANT.get("gate_own_ring") else c[2 + ax] + a - STREET   # an outer gate: from its inner street
        for i in range(-int(lim // 5), int(lim // 5) + 1):
            t = 5 * i
            rect = self.gate_rect(side, a, t, r0)
            if any(Map.gap(rect, p["rect"]) < 0 for p in self.placed):
                continue                                  # a building stands there already (an inner gate is fine)
            if VARIANT.get("gate_no_mine") and any(Map.gap(rect, (sq[0], sq[1], MINE_WALL, MINE_WALL)) < 0
                                                  for _, sq in self.mine_sources()):
                continue                                  # a mine stands in it: no way through, not a gate
            out.append({"id": f"g{len(out)}", "t": t, "rect": rect,
                        "text": f"a gate one street wide {self.gate_where(side, t)} of the {ORD[k]} ring: "
                                + self.gate_words(side, k, a, t)})
        out.append({"id": f"g{len(out)}", "t": None, "rect": None,
                    "text": f"no gate on the {side} of the {ORD[k]} ring: that side stays closed"})
        return out

    def state(self) -> str:
        s = super().state()
        gs = getattr(self, "gate_list", [])
        if gs:
            by = {}
            for g in gs:
                by.setdefault(g["ring"], []).append(f"{g['side']}: " + (self.gate_where(g["side"], g["t"]) if g["t"] is not None
                                                                      else "none, closed"))
            s += "\n" + " ".join(f"Gates of the {ORD[k]} ring, kept free: {'; '.join(v)}." for k, v in sorted(by.items()))
        return s


TOWN_BUILDERS = {"town1": TownBuilder, "town2": TownBuilder, "town3": TownBuilder3, "town4": TownBuilder4, "town5": TownBuilder4,
                 "town6": TownBuilder6, "town7": TownBuilder7, "town8": TownBuilder8, "town9": TownBuilder9, "town10": TownBuilder9,
                 "town11": TownBuilder9, "town12": TownBuilder12, "town13": TownBuilder12, "town14": TownBuilder12,
                 "town15": TownBuilder15, "town16": TownBuilder15, "town17": TownBuilder15, "town18": TownBuilder15, "town19": TownBuilder15, "town20": TownBuilder15, "town21": TownBuilder15, "town22": TownBuilder15}


TOWN_TEMPLATE = """Role: City planner.
Goal: {goal}
Map: a real map seen from above. A street is about {street} m wide. The new {kind} is {w} m wide and {d} m deep.
{state}
Question {n} of {total}: where does the new {kind} go?"""


MAX_OPTIONS = 255    # the Jev endpoint answers 503 above 255 criteria (measured 2026-10-07: 250 ok, 256 refused);
                     # a question with more is split in two: which side, then which place there (ask_two_steps)


def town_build(b: TownBuilder, goal: str, n: int, total: int):
    """Spots with the same words are one option to Jev (it cannot tell them apart): the first, nearest the CC."""
    options, seen = [], set()
    for r in b.spots():
        t = b.option_text(r)
        if t not in seen:
            seen.add(t)
            options.append({"id": f"s{len(options)}", "rect": r, "text": t})
    k = b.new
    prompt = TOWN_TEMPLATE.format(goal=goal, street=STREET, kind=k["kind"], w=round(k["w"], 1), d=round(k["d"], 1),
                                  state=b.state(), n=n, total=total)
    return prompt, options


def rect_world(m: Map, r, kind: str) -> dict:
    """A CC-frame rect as the page draws it: centre, angle, width along the angle's local x."""
    x, z = m.wld(r[0], r[1])
    return {"kind": kind, "x": round(x, 2), "z": round(z, 2), "a": m.a, "w": 2 * r[2], "d": 2 * r[3]}


def town_maps(snaps: dict, minute: int, placed: list, dropped: dict, hsize: dict):
    """That minute's map without Petra's City Planner buildings and without her structures that stand where a
    City Planner building stands (dropped from then on)."""
    snap = json.loads(json.dumps(snaps[minute]))
    snap["structures"] = [st for st in snap["structures"] if not planner_struct(st) and skey(st) not in dropped]
    m = Map(snap, hsize)
    new_drops = [st for st in snap["structures"] if "civil_centre" not in st["tpl"] and
                 any(Map.gap(m.rect(st), p["rect"]) < 0 for p in placed)]
    for st in new_drops:
        dropped[skey(st)] = minute
    snap["structures"] = [st for st in snap["structures"] if skey(st) not in dropped]
    return Map(snap, hsize), new_drops


def ask_two_steps(b, prompt, options, n):
    """More options than the endpoint takes (255): split the question in two, so Jev makes both choices and code
    ranks nothing.  First: on which side or corner of the civic centre (every side that has a place, each with every
    kind of place it offers); then: which place there, among all of that side's options."""
    groups = {}
    for o in options:
        groups.setdefault(b.face_word(o["rect"]), []).append(o)
    kind = b.new["kind"]
    first = []
    for i, (fw, opts) in enumerate(groups.items()):
        heads = list(dict.fromkeys(o["text"].split(": ", 1)[1].split(", ")[0].split("; ")[0] for o in opts))
        where = f"at the {fw} of the civic centre" if "corner" in fw else f"on the {fw} of the civic centre"
        first.append({"id": f"w{i}", "side": fw, "text": f"a {kind} {where}: {len(opts)} places there: " + "; ".join(heads)})
    q1 = prompt.rsplit("\n", 1)[0] + f"\nQuestion {n}, first part: on which side of the civic centre does the new {kind} go?"
    r1 = cd.ask_retry(q1, first, f"{n} side", f"On which side of the civic centre does the new {kind} go?")
    if not r1.get("ok"):
        return r1, None
    side = next(o for o in first if o["id"] == r1["choice"])
    r2 = cd.ask_retry(prompt, groups[side["side"]], f"{n} spot")
    return r2, {"side": side["side"], "text": side["text"], "p": (r1.get("probabilities") or {}).get(r1["choice"]),
                "sides": len(first), "of": len(groups[side["side"]])}


def ask_gates(b, m, goal, placed, gates, run, n, dry) -> str | None:
    """If the building just placed started a ring, ask the four gate questions of that ring."""
    g = TownBuilder15(m, goal, placed, b.new)
    g.gate_rects, g.gate_list = [x["rect"] for x in gates if x["rect"]], gates
    bands = g.bands(placed)
    k = len(bands)
    if len(bands[-1][2]) != 1 or bands[-1][2][0] is not placed[-1] or any(x["ring"] == k for x in gates):
        return None
    p = placed[-1]
    first = f"a {p['kind']} {g.where_phrase(p['rect'])}, {g.gap_words(g.din(p['rect']), 'the civic centre') if k == 1 else g.gap_words(g.street_to(p['rect'], bands[k - 2][2]), 'the ' + ORD[k - 1] + ' ring')}"
    for side in GATE_SIDES:
        options = g.gate_options(k, side, bands[-1])
        prompt = GATE_TEMPLATE.format(goal=goal, street=STREET, state=g.state(), ring=ORD[k], first=first, side=side)
        instr = f"Where is the gate on the {side} of the {ORD[k]} ring?"
        byid = {o["id"]: o for o in options}
        if dry or len(options) == 1:                      # one option: nothing to ask
            r = {"ok": True, "choice": options[0]["id"], "probabilities": {o["id"]: (1.0 if i == 0 else 0.0) for i, o in enumerate(options)}}
        else:
            r = cd.ask_retry(prompt, options, f"{n} gate {side}", instr)
        if not r.get("ok"):
            return f"q{n} gate {side}: {r.get('error')}"
        pick, probs = byid[r["choice"]], r.get("probabilities") or {}
        gates.append({"ring": k, "side": side, "t": pick["t"], "rect": pick["rect"], "a": bands[-1][0]})
        g.gate_rects = [x["rect"] for x in gates if x["rect"]]
        top = sorted(probs, key=lambda i: -probs[i])[:5]
        run["gates"].append({"after": n, "ring": k, "side": side, "t": pick["t"], "text": pick["text"], "p": probs.get(r["choice"]),
                             "prompt": prompt, "n_options": len(options),
                             "top": [{"id": i, "p": probs[i], "text": byid[i]["text"]} for i in top if i in byid],
                             "rect": rect_world(m, pick["rect"], "gate") if pick["rect"] else None})
        if cd.VERBOSE:
            print(f"   gate ring {k} {side}: {len(options)} opts -> p={probs.get(r['choice'])}: {pick['text'][:150]}", flush=True)
    return None


def town_run(civ: str, dry: bool, until: int, goal_text: str | None = None, line: str | None = None) -> dict:
    snaps = load_all(TOWN_LOG)
    events = queue_events(TOWN_LOG, until)
    hsize = {"w": PITCH, "d": PITCH, "tpl": "house"}
    goal = goal_text or civ_line(civ, line)
    now = datetime.now()
    run = {"id": f"{now:%Y%m%d-%H%M%S}-{VARIANT['name']}-{civ}" + ("-prop" if goal_text else "") + (f"-{line}" if line else "")
                 + ("-dry" if dry else ""),
           "mode": "real", "town": True, "timeline": True, "variant": VARIANT["name"],
           "civ": civ + (" (proposed line)" if goal_text else f" ({line} line)" if line else ""), "dry": dry, "goal_text": goal,
           "started": now.isoformat(timespec="seconds"),
           "source": {"log": str(TOWN_LOG), "until": until, "minutes": [e["minute"] for e in events],
                      "events": [{k: e[k] for k in ("minute", "t", "tpl", "kind")} for e in events]},
           "template": TOWN_TEMPLATE, "instructions": "Where does the new building go?",
           "rule": f"spots flush ({FLUSH:g} m) against a face of any standing building or one street ({STREET} m) from it, "
                   "lined up with either end of that face; impossible spots removed (territory, overlap, resources)",
           "maps": {}, "dropped": [], "steps": [], "summary": {}, "flags": dict(VARIANT), "gates": []}
    placed: list = []
    dropped: dict = {}
    gates: list = []          # picked gates: {"ring", "side", "t", "rect"} (rect None = no gate on that side)
    for n, ev in enumerate(events, 1):
        mnt = ev["minute"]
        m, drops = town_maps(snaps, mnt, placed, dropped, hsize)
        for st in drops:
            run["dropped"].append({"minute": mnt, "tpl": st["tpl"], "x": st["x"], "z": st["z"], "a": st["a"],
                                   "w": st["w"], "d": st["d"], "done": st["done"]})
        if str(mnt) not in run["maps"]:
            run["maps"][str(mnt)] = map_payload(m)
        run.setdefault("map", map_payload(m))
        b = TOWN_BUILDERS.get(VARIANT["name"], TownBuilder3)(m, goal, placed, ev)
        b.gate_rects = [g["rect"] for g in gates if g["rect"]]
        b.gate_list = gates
        prompt, options = town_build(b, goal, n, len(events))
        if not options:
            run["summary"]["error"] = f"q{n}: no possible spot for the {ev['kind']}"
            break
        byid = {o["id"]: o for o in options}
        t0 = time.time()
        parts = None
        if dry:
            r = {"ok": True, "choice": options[0]["id"],
                 "probabilities": {o["id"]: (1.0 if i == 0 else 0.0) for i, o in enumerate(options)}}
        elif len(options) > MAX_OPTIONS:
            r, parts = ask_two_steps(b, prompt, options, n)
        else:
            r = cd.ask_retry(prompt, options, n)
        if not r.get("ok"):
            run["summary"]["error"] = f"q{n}: {r.get('error')}"
            run["steps"].append({"n": n, "minute": mnt, "prompt": prompt, "error": r.get("error")})
            break
        pick = byid[r["choice"]]
        placed.append({"kind": ev["kind"], "rect": pick["rect"]})
        probs = r.get("probabilities") or {}
        top = sorted(probs, key=lambda i: -probs[i])[:5]
        run["steps"].append({
            "n": n, "minute": mnt, "kind": ev["kind"], "tpl": ev["tpl"], "prompt": prompt, "n_options": len(options),
            "slots": {o["id"]: rect_world(m, o["rect"], ev["kind"]) for o in options},
            "top": [{"id": i, "p": probs[i], "text": byid[i]["text"]} for i in top if i in byid],
            "choice": r["choice"], "text": pick["text"], "p": probs.get(r["choice"]), "rect": list(pick["rect"]), "parts": parts,
            "building": rect_world(m, pick["rect"], ev["kind"]), "ms": r.get("ms") or round((time.time() - t0) * 1000)})
        run["summary"] = {"buildings": len(placed), "houses": len(placed), "touching_another": None,
                          "dropped": len(run["dropped"])}
        if cd.VERBOSE:
            print(f"q{n:2d} m{mnt:2d} {ev['kind']:13s} {len(options):3d} opts -> p={probs.get(r['choice'])}: {pick['text'][:170]}",
                  flush=True)
        if VARIANT.get("gate_q") and isinstance(b, TownBuilder15):
            err = ask_gates(b, m, goal, placed, gates, run, n, dry)
            if err:
                run["summary"]["error"] = err
                save_real(run)
                break
        save_real(run)
    run["buildings"] = [s["building"] for s in run["steps"] if s.get("building")]
    save_real(run)
    kinds = {}
    for d in run["dropped"]:
        kinds[short(d["tpl"])] = kinds.get(short(d["tpl"]), 0) + 1
    print(f"run {run['id']}: {len(placed)}/{len(events)} buildings, dropped {kinds}"
          + (f", error: {run['summary']['error']}" if run["summary"].get("error") else ""), flush=True)
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--civ", required=True, help="civ code: any civs/<civ>.json in the strategos mod")
    ap.add_argument("--minute", type=int, default=10)
    ap.add_argument("--houses", type=int, default=12)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--dry", action="store_true", help="no Jev: take the first option (nearest the CC)")
    ap.add_argument("--print", type=int, default=0, help="print the prompt + options of question N (dry houses before it)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--variant", default="real1",
                    help="real1 | real2 (lines say around/away from the civic centre) | pieces | a town variant name")
    ap.add_argument("--goal-text", default=None, help="test a proposed civ line (the civ JSON is not touched)")
    ap.add_argument("--line", default=None, help="--town: read the layout line from city_data/civ_overrides/<civ>.<line>.json")
    ap.add_argument("--set", action="append", default=[], help="extra VARIANT flag for a variant, e.g. apart_words=1")
    ap.add_argument("--flags", default="", help="comma list of VARIANT flags to switch on")
    ap.add_argument("--timeline", action="store_true",
                    help="real timeline: question k at the minute Petra's k-th house appears, that minute's map")
    ap.add_argument("--town", action="store_true",
                    help="every City Planner building Petra queued (city_data/planner_classes.json), asked at the minute "
                         "she queued it, on that minute's map (source: TOWN_LOG)")
    ap.add_argument("--until", type=int, default=20, help="--town: last game minute of the source run")
    args = ap.parse_args(argv)
    cd.VERBOSE = args.verbose
    VARIANT.update(name=args.variant, around=args.variant == "real2", pieces=args.variant == "pieces")
    for kv in args.set + [f"{f}=1" for f in args.flags.split(",") if f]:
        k, v = kv.split("=", 1)
        VARIANT[k] = False if v in ("0", "false", "") else True if v == "1" else v
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
        if args.town:
            town_run(args.civ, args.dry, args.until, args.goal_text, args.line)
        elif args.timeline:
            timeline_run(args.civ, args.dry, args.goal_text)
        else:
            one_run(args.civ, args.minute, args.houses, args.dry, args.goal_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
