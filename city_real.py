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


def civ_line(civ: str) -> str:
    """The civ's town-layout line from the named field (city_data/civ_layout.json), civ JSON first, then the
    lab-side override.  No per-civ code and no text[] index."""
    def get(d):
        for part in LAYOUT["field"].split("."):
            d = d.get(part) if isinstance(d, dict) else None
        return d
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
                    "wall": f"built against {what}"}[sw]
        if g < 8:
            return {"plain": f"{metres(g)} from {what}, narrower than a street",
                    "conseq": f"{metres(g)} from {what}: too narrow for a street",
                    "wall": f"{metres(g)} from {what}, a narrow lane"}[sw]
        if g <= 12:
            return {"plain": f"one street ({metres(g)}) from {what}", "conseq": f"one street ({metres(g)}) from {what}",
                    "wall": f"a street ({metres(g)}) between it and {what}"}[sw]
        return {"plain": f"{metres(g)} from {what}, more than one street",
                "conseq": f"{metres(g)} from {what}: wider than one street",
                "wall": f"{metres(g)} from {what}, more than a street"}[sw]

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
        if g <= TOL:
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


TOWN_BUILDERS = {"town1": TownBuilder, "town2": TownBuilder, "town3": TownBuilder3, "town4": TownBuilder4, "town5": TownBuilder4}


TOWN_TEMPLATE = """Role: City planner.
Goal: {goal}
Map: a real map seen from above. A street is about {street} m wide. The new {kind} is {w} m wide and {d} m deep.
{state}
Question {n} of {total}: where does the new {kind} go?"""


MAX_OPTIONS = 255    # the Jev endpoint answers 503 above 255 criteria (measured 2026-10-07: 250 ok, 256 refused)


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


def town_run(civ: str, dry: bool, until: int, goal_text: str | None = None) -> dict:
    snaps = load_all(TOWN_LOG)
    events = queue_events(TOWN_LOG, until)
    hsize = {"w": PITCH, "d": PITCH, "tpl": "house"}
    goal = goal_text or civ_line(civ)
    now = datetime.now()
    run = {"id": f"{now:%Y%m%d-%H%M%S}-{VARIANT['name']}-{civ}" + ("-prop" if goal_text else "") + ("-dry" if dry else ""),
           "mode": "real", "town": True, "timeline": True, "variant": VARIANT["name"],
           "civ": civ + (" (proposed line)" if goal_text else ""), "dry": dry, "goal_text": goal,
           "started": now.isoformat(timespec="seconds"),
           "source": {"log": str(TOWN_LOG), "until": until, "minutes": [e["minute"] for e in events],
                      "events": [{k: e[k] for k in ("minute", "t", "tpl", "kind")} for e in events]},
           "template": TOWN_TEMPLATE, "instructions": "Where does the new building go?",
           "rule": f"spots flush ({FLUSH:g} m) against a face of any standing building or one street ({STREET} m) from it, "
                   "lined up with either end of that face; impossible spots removed (territory, overlap, resources)",
           "maps": {}, "dropped": [], "steps": [], "summary": {}, "flags": dict(VARIANT)}
    placed: list = []
    dropped: dict = {}
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
        prompt, options = town_build(b, goal, n, len(events))
        if not options:
            run["summary"]["error"] = f"q{n}: no possible spot for the {ev['kind']}"
            break
        if len(options) > MAX_OPTIONS:
            run["summary"]["error"] = f"q{n}: {len(options)} different options, more than the endpoint takes ({MAX_OPTIONS})"
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
        pick = byid[r["choice"]]
        placed.append({"kind": ev["kind"], "rect": pick["rect"]})
        probs = r.get("probabilities") or {}
        top = sorted(probs, key=lambda i: -probs[i])[:5]
        run["steps"].append({
            "n": n, "minute": mnt, "kind": ev["kind"], "tpl": ev["tpl"], "prompt": prompt, "n_options": len(options),
            "slots": {o["id"]: rect_world(m, o["rect"], ev["kind"]) for o in options},
            "top": [{"id": i, "p": probs[i], "text": byid[i]["text"]} for i in top if i in byid],
            "choice": r["choice"], "text": pick["text"], "p": probs.get(r["choice"]), "rect": list(pick["rect"]),
            "building": rect_world(m, pick["rect"], ev["kind"]), "ms": r.get("ms") or round((time.time() - t0) * 1000)})
        run["summary"] = {"buildings": len(placed), "houses": len(placed), "touching_another": None,
                          "dropped": len(run["dropped"])}
        save_real(run)
        if cd.VERBOSE:
            print(f"q{n:2d} m{mnt:2d} {ev['kind']:13s} {len(options):3d} opts -> p={probs.get(r['choice'])}: {pick['text'][:170]}",
                  flush=True)
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
            town_run(args.civ, args.dry, args.until, args.goal_text)
        elif args.timeline:
            timeline_run(args.civ, args.dry, args.goal_text)
        else:
            one_run(args.civ, args.minute, args.houses, args.dry, args.goal_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
