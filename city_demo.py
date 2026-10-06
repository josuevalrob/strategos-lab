#!/usr/bin/env python3
"""City planner experiment: ask Jev where each next house goes, one house at a time.

    python3 city_demo.py [--variant facts] [--goal square6] [--runs 1] [--steps N]
    python3 city_demo.py --variant facts --probe "2,2 3,2"     # one question on a given map

Each answer places a house; the next prompt shows it.  Code only removes impossible cells
(off the grid, the civic centre, a house) and applies the variant's one locality rule; Jev's
choice is used as given.  The prompt/option builder never sees the scoring target: the shape
lives only in the goal text (GOALS), the target only in the scoring section at the bottom.
Asks go through the running lab (POST /api/ask), never to Jev directly.
Runs land in static/city/runs/<id>.json (+ index.json), shown by /static/city.html.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

LAB = os.environ.get("LAB_URL", "http://127.0.0.1:8765").rstrip("/")
RUNS_DIR = Path(__file__).resolve().parent / "static" / "city" / "runs"
CARRIER = {"run": "runs/solo/20261006-132405", "idx": 2683, "qkey": "house_build"}

VERBOSE = False
GRID = 10
CC = [(4, 4), (5, 4), (4, 5), (5, 5)]
CCX = (min(x for x, _ in CC), max(x for x, _ in CC))
CCY = (min(y for _, y in CC), max(y for _, y in CC))

# == goals: the shape lives only in this text ==================================================
GOALS = {
    "square6": {"tier": "A", "houses": 20, "text": (
        "Build a hollow square of houses around the civic centre: 6 houses along each side, corners "
        "shared, 20 houses in all, each house touching its neighbours along the side. Leave one empty "
        "street, 1 cell wide, between the civic centre and the houses.")},
    # generalization: only the goal text (here) and the scoring target (TARGETS) change
    "square4": {"tier": "A", "houses": 12, "text": (
        "Build a hollow square of houses around the civic centre: 4 houses along each side, corners "
        "shared, 12 houses in all, each house touching its neighbours along the side. The houses stand "
        "right against the civic centre, with no street between them and the civic centre.")},
    "square8": {"tier": "A", "houses": 28, "text": (
        "Build a hollow square of houses around the civic centre: 8 houses along each side, corners "
        "shared, 28 houses in all, each house touching its neighbours along the side. Leave a gap of "
        "two empty cells between the civic centre and the houses.")},
    "triangle": {"tier": "A", "houses": 13, "text": (
        "Build the outline of a right triangle of houses, with the civic centre on its long side. The right "
        "angle is north-west of the civic centre, off its north-west corner. One leg is an "
        "east-west row north of the civic centre, from off its north-west corner to off its north-east "
        "corner; the other leg is a north-south line west of the civic centre, from off its north-west "
        "corner to off its south-west corner. Both legs keep one empty street, 1 cell wide, between them and "
        "the civic centre. The long side runs diagonally from the east end of the north leg, south-west "
        "through the civic centre, to the south end of the west leg; its two houses touch the north-east "
        "corner and the south-west corner of the civic centre. Every new house touches a house already "
        "built. Build both legs first; the long side comes last. 13 houses in all.")},
    "triangle_se": {"tier": "A", "houses": 15, "text": (
        "Build the outline of a right triangle of houses south-east of the civic centre. The right angle is "
        "the house in the south-east corner of the map, on both the bottom edge and the right edge. One leg "
        "is a row along the bottom edge of the map, from the corner house west to directly south of the "
        "civic centre, below its west half; the other leg is a line along the right edge of the map, from the "
        "corner house north to directly east of the civic centre, beside its north half. The long side is a diagonal line of houses that runs "
        "north-east, step by step, from the end of the bottom leg to the end of the right leg, facing the "
        "civic centre. Build both legs first; the long side comes last. 15 houses in all.")},
}

# == builder (shape-agnostic: reads the map, never the target) ================================
N_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def num(n: int) -> str:
    return N_WORDS[n] if 0 <= n < len(N_WORDS) else str(n)


def cell_txt(c) -> str:
    return f"({c[0]}, {c[1]})"


def cc_dxy(c):
    dx = max(CCX[0] - c[0], c[0] - CCX[1], 0)
    dy = max(CCY[0] - c[1], c[1] - CCY[1], 0)
    return dx, dy


def cc_gap(c) -> int:
    """Empty cells between the cell and the civic centre (any direction)."""
    dx, dy = cc_dxy(c)
    return max(dx, dy) - 1


def side(c) -> str:
    ns = "north" if c[1] < CCY[0] else "south" if c[1] > CCY[1] else ""
    ew = "west" if c[0] < CCX[0] else "east" if c[0] > CCX[1] else ""
    return f"{ns}-{ew}" if ns and ew else ns or ew


def run_len(c, d, hs) -> int:
    n, (x, y) = 0, c
    while (x + d[0] * (n + 1), y + d[1] * (n + 1)) in hs:
        n += 1
    return n


def edge_dist(c) -> int:
    return min(c[0], c[1], GRID - 1 - c[0], GRID - 1 - c[1])


def gap_phrase(c, street=True, corner_name=False, wide=False) -> str:
    dx, dy = cc_dxy(c)
    g = max(dx, dy) - 1
    if g == 0 and corner_name and dx == 1 and dy == 1:
        return f"touches the {side(c)} corner of the civic centre, no street between"
    if g >= 2 and wide:
        return f"{num(g)} empty cells between it and the civic centre, more than one street"
    if g == 0:
        tail = ", no street between" if street else ""
        return ("touches a corner of the civic centre" if dx == 1 and dy == 1
                else "touches the civic centre") + tail
    if g == 1:
        return "one street (1 empty cell) between it and the civic centre" if street else \
            "one empty cell between it and the civic centre"
    return f"{num(g)} empty cells between it and the civic centre"


def line_phrases(c, hs) -> list:
    out = []
    l, r = run_len(c, (-1, 0), hs), run_len(c, (1, 0), hs)
    u, d = run_len(c, (0, -1), hs), run_len(c, (0, 1), hs)
    for a, b, name in ((l, r, "east-west row"), (u, d, "north-south line")):
        if a and b:
            out.append(f"joins two {name}s into one {name} of {num(a + b + 1)} houses")
        elif a or b:
            k = a + b
            out.append(f"extends the {name} of {num(k)} house{'s' if k > 1 else ''} to {num(k + 1)} houses")
    if (l or r) and (u or d):
        out.append("makes a corner where an east-west row meets a north-south line")
    return out


def touch_phrase(c, hs) -> str:
    orth = sum((c[0] + dx, c[1] + dy) in hs for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
    diag = sum((c[0] + dx, c[1] + dy) in hs for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)))
    if orth:
        return f"touches {num(orth)} house{'s' if orth > 1 else ''} side by side"
    if diag:
        return "touches a house only at a corner"
    return "touches no house"


def edge_phrase(c) -> str:
    e = edge_dist(c)
    return "on the map edge" if e == 0 else f"{num(e)} cell{'s' if e > 1 else ''} from the map edge"


DIRS = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north",
        (1, 1): "south-east", (-1, 1): "south-west", (1, -1): "north-east", (-1, -1): "north-west"}


def pos_phrase(c) -> str:
    s = side(c)
    return f"directly {s} of the civic centre" if "-" not in s else f"{s} of the civic centre"


def pos2_phrase(c) -> str:
    """Which face of the civic centre the cell looks at: directly / slightly off / diagonally off a corner."""
    dx, dy = cc_dxy(c)
    ns = "north" if c[1] < CCY[0] else "south" if c[1] > CCY[1] else ""
    ew = "west" if c[0] < CCX[0] else "east" if c[0] > CCX[1] else ""
    if not ew:
        return f"directly {ns} of the civic centre"
    if not ns:
        return f"directly {ew} of the civic centre"
    if dx == dy:
        return f"diagonally {ns}-{ew} of the civic centre, off its {ns}-{ew} corner"
    return (f"{ns} of the civic centre, slightly {ew}" if dy > dx else f"{ew} of the civic centre, slightly {ns}")


def pos3_phrase(c) -> str:
    """pos2 without the word 'diagonally', and which half of the civic centre a straight-on cell faces."""
    ns = "north" if c[1] < CCY[0] else "south" if c[1] > CCY[1] else ""
    ew = "west" if c[0] < CCX[0] else "east" if c[0] > CCX[1] else ""
    dx, dy = cc_dxy(c)
    if not ew:
        half = "west" if c[0] == CCX[0] else "east"
        return f"directly {ns} of the civic centre, {'above' if ns == 'north' else 'below'} its {half} half"
    if not ns:
        half = "north" if c[1] == CCY[0] else "south"
        return f"directly {ew} of the civic centre, beside its {half} half"
    if dx == dy:
        return f"{ns}-{ew} of the civic centre, off its {ns}-{ew} corner"
    return (f"{ns} of the civic centre, slightly {ew}" if dy > dx else f"{ew} of the civic centre, slightly {ns}")


def map_edge_phrases(c) -> list:
    ns = "north" if c[1] == 0 else "south" if c[1] == GRID - 1 else ""
    ew = "west" if c[0] == 0 else "east" if c[0] == GRID - 1 else ""
    words = {"north": "top", "south": "bottom", "west": "left", "east": "right"}
    if ns and ew:
        return [f"in the {ns}-{ew} corner of the map, on the {words[ns]} edge and the {words[ew]} edge"]
    if ns or ew:
        return [f"on the {words[ns or ew]} ({ns or ew}) edge of the map"]
    return []


def move_phrases(c, hs) -> list:
    """How the cell continues the houses next to it: straight on, a turn, a branch, a diagonal step."""
    out = []
    for d, name in DIRS.items():
        n = (c[0] - d[0], c[1] - d[1])          # c = n + d
        if n not in hs:
            continue
        back = (n[0] - d[0], n[1] - d[1])
        if d[0] and d[1]:
            out.append(f"continues the diagonal line {name}wards" if back in hs
                       else f"steps diagonally {name} from a house, touching it at a corner")
            continue
        axis = "east-west row" if d[1] == 0 else "north-south line"
        if back in hs:
            out.append(f"extends the {axis} {name}wards")
            continue
        perp = [(n[0] + d[1], n[1] + d[0]), (n[0] - d[1], n[1] - d[0])]
        k = sum(p in hs for p in perp)
        out.append("goes " + name + (" from a single house" if k == 0 else
                                     ", turning at the end of a line and making a corner" if k == 1 else
                                     ", branching off the middle of a line"))
    return out


AXES = {(1, 0): "east-west row", (0, 1): "north-south line",
        (1, 1): "diagonal line (north-west to south-east)", (1, -1): "diagonal line (south-west to north-east)"}
ENDS = {(1, 0): ("west", "east"), (0, 1): ("north", "south"), (1, 1): ("north-west", "south-east"),
        (1, -1): ("south-west", "north-east")}


RUNS_STRICT = {"on": False}   # set per variant in build()


def runs(hs) -> list:
    """Maximal straight runs of 2+ houses: [(axis, [cells from first to last end])]."""
    out = []
    for d in AXES:
        for c in sorted(hs):
            if (c[0] - d[0], c[1] - d[1]) in hs:
                continue
            cells = [c]
            while (cells[-1][0] + d[0], cells[-1][1] + d[1]) in hs:
                cells.append((cells[-1][0] + d[0], cells[-1][1] + d[1]))
            if len(cells) >= 2:
                out.append((d, cells))
    # a 2-house run that crosses the middle of a longer run is a side effect, not a line
    inner = {c for _, cells in out if len(cells) >= 3 for c in cells[1:-1]}
    out = [(d, cells) for d, cells in out if len(cells) >= 3 or not inner.intersection(cells)]
    if RUNS_STRICT["on"]:   # ... and so is a 2-house run whose both houses already belong to longer runs
        longc = {c for _, cells in out if len(cells) >= 3 for c in cells}
        out = [(d, cells) for d, cells in out if len(cells) >= 3 or not set(cells) <= longc]
    return out


AXIS_NAMES = {"on": False}   # set per variant in build()


def where_cells(cells, d=None) -> str:
    """Where a group of cells lies: along a map edge, else its side of the civic centre and gap."""
    for test, name in ((lambda c: c[1] == 0, "top"), (lambda c: c[1] == GRID - 1, "bottom"),
                       (lambda c: c[0] == 0, "left"), (lambda c: c[0] == GRID - 1, "right")):
        if all(test(c) for c in cells):
            return f"along the {name} edge of the map"
    if AXIS_NAMES["on"] and d == (0, 1) and all(c[0] < CCX[0] for c in cells):
        rel = "west of the civic centre"       # a north-south line is named by its west/east side
    elif AXIS_NAMES["on"] and d == (0, 1) and all(c[0] > CCX[1] for c in cells):
        rel = "east of the civic centre"
    elif all(c[1] < CCY[0] for c in cells):
        rel = "north of the civic centre"
    elif all(c[1] > CCY[1] for c in cells):
        rel = "south of the civic centre"
    elif all(c[0] < CCX[0] for c in cells):
        rel = "west of the civic centre"
    elif all(c[0] > CCX[1] for c in cells):
        rel = "east of the civic centre"
    else:
        rel = "around the civic centre"
    g = min(cc_gap(c) for c in cells)
    gap = ("touching it" if g == 0 else "one street from it" if g == 1 else f"{num(g)} empty cells from it")
    return f"{rel}, {gap}"


def run_name(d, cells, gap=True) -> str:
    w = where_cells(cells, d)
    return f"{AXES[d]} {w if gap else w.split(',')[0]}"


POS = {"fn": None}   # end/single naming: pos_phrase unless a variant sets pos2 (set in build)


def end_txt(c) -> str:
    corner = map_edge_phrases(c)
    return (POS["fn"] or pos_phrase)(c) + (f", {corner[0].split(',')[0]}" if corner and "corner" in corner[0] else "")


def state_lines(houses):
    hs = set(houses)
    rs = runs(hs)
    if not hs:
        return "Lines so far: none."
    parts = []
    for d, cells in sorted(rs, key=lambda r: -len(r[1])):
        a, b = ENDS[d]
        parts.append(f"{'an' if AXES[d][0] in 'ae' else 'a'} {run_name(d, cells)}, {num(len(cells))} houses long, "
                     f"its {a} end {end_txt(cells[0])}, its {b} end {end_txt(cells[-1])}")
    in_run = {c for _, cells in rs for c in cells}
    singles = [c for c in houses if c not in in_run]
    out = "Lines so far: " + ("; ".join(parts) if parts else "none") + "."
    if singles:
        out += " Single houses: " + "; ".join(f"one {end_txt(c)}, {gap_phrase(c)}" for c in singles) + "."
    return out


def anchor_phrases(c, hs, rs=None, short=False, length=True, v4=False) -> list:
    """How the cell continues the lines next to it, naming each line by where it lies."""
    rs = runs(hs) if rs is None else rs
    run_name_ = (lambda d, cells: run_name(d, cells, gap=False)) if short else run_name
    single = end_txt if short else pos_phrase
    out = []
    for d, name in DIRS.items():
        n = (c[0] - d[0], c[1] - d[1])          # c = n + d
        if n not in hs:
            continue
        diag = bool(d[0] and d[1])
        mine = [(ax, cells) for ax, cells in rs if n in cells]
        same = [(ax, cells) for ax, cells in mine if ax in (d, (-d[0], -d[1]))]
        if same:
            ax, cells = same[0]
            out.append(f"extends the {run_name_(ax, cells)} {name}wards"
                       + (f", making it {num(len(cells) + 1)} houses long" if length else ""))
            continue
        ends = [(ax, cells) for ax, cells in mine if n in (cells[0], cells[-1])]
        if ends:
            ax, cells = ends[0]
            which = ENDS[ax][0] if n == cells[0] else ENDS[ax][1]
            verb = "steps diagonally" if diag else "turns" if not (ax[0] and ax[1]) else "goes"
            out.append(f"{verb} {name} from the {which} end of the {run_name_(ax, cells)}"
                       + (", making a corner" if verb == "turns" and not v4 else ""))
        elif mine:
            ax, cells = mine[0]
            out.append(f"{'steps diagonally' if diag else 'branches'} {name} off the middle of the {run_name_(ax, cells)}")
        elif v4 and not diag:
            out.append(f"starts {'an east-west row' if d[1] == 0 else 'a north-south line'} {name}wards "
                       f"from the single house {single(n)}")
        else:
            out.append(f"{'steps diagonally' if diag else 'goes'} {name} from the single house {single(n)}")
    return out


def anchor_main(c, hs, v4=False) -> list:
    """Only the strongest relations: extending a line > leaving a line's end > a single house > a
    line's middle; 'touches no house' when nothing is next to it."""
    ph = anchor_phrases(c, hs, short=True, length=False, v4=v4)
    if not ph:
        return ["touches no house"]
    for key in ("extends the", " end of the ", "single house", "middle"):
        hit = [p for p in ph if key in p]
        if hit:
            return hit[:2]
    return ph[:2]


# -- candidate rules: impossible cells always go; ONE shape-agnostic locality rule -----------
def _free(houses):
    taken = set(CC) | set(houses)
    return [(x, y) for y in range(GRID) for x in range(GRID) if (x, y) not in taken]


def _near(houses, reach):
    near = list(CC) + list(houses)
    return [c for c in _free(houses) if any(max(abs(c[0] - a), abs(c[1] - b)) <= reach for a, b in near)]


def _near2(houses, r_house, r_cc):
    return [c for c in _free(houses)
            if any(max(abs(c[0] - a), abs(c[1] - b)) <= r_house for a, b in houses)
            or any(max(abs(c[0] - a), abs(c[1] - b)) <= r_cc for a, b in CC)]


RULES = {
    "all": ("every free cell", lambda houses: _free(houses)),
    "touch": ("free cells touching a house (side or corner); on an empty map every free cell",
              lambda houses: [c for c in _free(houses) if not houses
                              or any(max(abs(c[0] - a), abs(c[1] - b)) <= 1 for a, b in houses)]),
    "adj1cc2": ("free cells touching a house (side or corner), or within 2 cells of the civic centre",
                lambda houses: _near2(houses, 1, 2)),
    "adj1cc3": ("free cells touching a house (side or corner), or within 3 cells of the civic centre",
                lambda houses: _near2(houses, 1, 3)),
    "reach3": ("free cells within 3 cells (any direction, diagonals count) of the civic centre or a house",
               lambda houses: _near(houses, 3)),
    "reach2": ("free cells within 2 cells (any direction, diagonals count) of the civic centre or a house",
               lambda houses: _near(houses, 2)),
}


# -- option styles --------------------------------------------------------------------------
def opts_coords(cells, houses, v):
    return [{"id": f"c{x}_{y}", "text": f"a house at ({x}, {y})", "cell": (x, y)} for x, y in cells]


FACTS = {   # fact name -> fn(cell, houses set) -> list of phrases
    "gap": lambda c, hs: [gap_phrase(c)],
    "gap_plain": lambda c, hs: [gap_phrase(c, street=False)],
    "gap2": lambda c, hs: [gap_phrase(c, corner_name=True, wide=True)],
    "gap_cn": lambda c, hs: [gap_phrase(c, corner_name=True)],
    "lines": lambda c, hs: line_phrases(c, hs),
    "touch": lambda c, hs: [touch_phrase(c, hs)],
    "edge": lambda c, hs: [edge_phrase(c)],
    "pos": lambda c, hs: [pos_phrase(c)],
    "medge": lambda c, hs: map_edge_phrases(c),
    "moves": move_phrases,
    "anchors": anchor_phrases,
    "anchors2": lambda c, hs: anchor_phrases(c, hs, short=True),
    "anchors3": lambda c, hs: anchor_phrases(c, hs, short=True, length=False),
    "amain": anchor_main,
    "amain4": lambda c, hs: anchor_main(c, hs, v4=True),
    "pos2": lambda c, hs: [pos2_phrase(c)],
    "pos3": lambda c, hs: [pos3_phrase(c)],
}


def opts_facts(cells, houses, v):
    hs = set(houses)
    out = []
    for c in cells:
        parts = [p for f in v.get("facts", ["gap", "lines", "touch", "edge"]) for p in FACTS[f](c, hs)]
        where = f"a house at {cell_txt(c)}, " if v.get("coords", True) else "a house "
        out.append({"id": f"c{c[0]}_{c[1]}", "cell": c,
                    "text": where + f"{side(c)} of the civic centre: " + "; ".join(parts)})
    return out


def opts_facts2(cells, houses, v):
    """No fixed prefix: every phrase comes from the variant's fact list."""
    hs = set(houses)
    out = []
    for c in cells:
        parts = [p for f in v["facts"] for p in FACTS[f](c, hs)]
        where = f"a house at {cell_txt(c)}: " if v.get("coords", True) else "a house: "
        out.append({"id": f"c{c[0]}_{c[1]}", "cell": c, "text": where + "; ".join(parts)})
    return out


OPTION_STYLES = {"coords": opts_coords, "facts": opts_facts, "facts2": opts_facts2}


# -- state styles ---------------------------------------------------------------------------
def state_list(houses):
    return "Houses built so far, in order: " + ("; ".join(
        f"{i} at {cell_txt(c)}" for i, c in enumerate(houses, 1)) or "none yet")


def state_count(houses):
    return f"Houses built so far: {num(len(houses))}."


def state_event(houses, verb=True, corner=True):
    """What the last answer changed, in words: where the last house is, what it did to the lines."""
    if not houses:
        return "Last house: none yet."
    last, before = houses[-1], set(houses[:-1])
    did = anchor_main(last, before, v4=True) if before else ["starts the build"]
    out = f"Last house: {end_txt(last)}, {gap_phrase(last, corner_name=True, wide=True)}" + (
        f"; it {'; it '.join(did)}." if verb else ".")
    mine = [(d, cells) for d, cells in runs(set(houses)) if last in cells]
    roles = []
    for d, cells in mine:
        a, b = ENDS[d]
        role = f"the {a} end" if last == cells[0] else f"the {b} end" if last == cells[-1] else "in the middle"
        roles.append(f"{role} of the {run_name(d, cells, gap=False)}, now {num(len(cells))} houses long")
    if roles:
        out += " It is now " + "; and ".join(roles) + "."
    rs = runs(set(houses)) if corner else []
    for d, cells in mine:                       # a corner: the other end of the last house's line ends another line
        if last not in (cells[0], cells[-1]):
            continue
        other = cells[-1] if last == cells[0] else cells[0]
        for d2, c2 in rs:
            if d2 != d and other in (c2[0], c2[-1]):
                which = ENDS[d2][0] if other == c2[0] else ENDS[d2][1]
                out += (f" Event: the last house turned a corner at the {which} end of the "
                        f"{run_name(d2, c2, gap=False)}: that line now meets the {run_name(d, cells, gap=False)}.")
                return out
    return out


STATE_STYLES = {"list": state_list, "count": state_count, "lines": state_lines, "event": state_event,
                "event_where": lambda houses: state_event(houses, verb=False, corner=False),
                "event_corner": lambda houses: state_event(houses, verb=False, corner=True)}

TEMPLATES = {
    "base": """Role: City planner.
Goal: {goal}
Map: a grid of cells, x from 0 (left, west) to {xmax} (right, east), y from 0 (top, north) to {ymax} (bottom, south). One house fills one cell.
The civic centre fills cells {cc}.
{state}
Question {n} of {total}: where does the next house go?
{options}""",
}
TEMPLATES["noopts"] = TEMPLATES["base"].replace("\n{options}", "")
INSTRUCTIONS = "Where does the next house go?"

# variant = template + state style(s) + option style + candidate rule (+ a note on what changed)
VARIANTS = {
    "coords": {"template": "base", "state": ["list"], "opts": "coords", "rule": "reach2",
               "note": "v1: bare coordinates"},
    "facts": {"template": "base", "state": ["list"], "opts": "facts", "rule": "reach2",
              "note": "options state generic facts: CC gap, side, rows extended, corner, touches, edge"},
    "facts_nolines": {"template": "base", "state": ["list"], "opts": "facts", "rule": "reach2",
                      "facts": ["gap", "touch", "edge"], "note": "facts without the row/line phrases"},
    "nl_count": {"template": "base", "state": ["count"], "opts": "facts", "rule": "reach2",
                 "facts": ["gap", "touch", "edge"], "note": "facts_nolines, state = house count only (no coords list)"},
    "nl_noedge": {"template": "base", "state": ["count"], "opts": "facts", "rule": "reach2",
                  "facts": ["gap", "touch"], "note": "nl_count without the map-edge fact"},
    "nl_nostreet": {"template": "base", "state": ["count"], "opts": "facts", "rule": "reach2",
                    "facts": ["gap_plain", "touch"], "note": "nl_noedge, gap without the word 'street'"},
    # -- round 2: one design for square + triangles --
    "t1": {"template": "base", "state": ["count"], "opts": "facts2", "rule": "all",
           "facts": ["pos", "gap", "medge", "moves", "touch"],
           "note": "nl_noedge + 8-way position ('directly north'), map-edge only when on it, moves (straight/turn/branch/diagonal with direction), every free cell"},
    "t2": {"template": "base", "state": ["count"], "opts": "facts2", "rule": "adj1cc2",
           "facts": ["pos", "gap", "medge", "moves", "touch"], "note": "t1 with rule adj1cc2"},
    "t3": {"template": "base", "state": ["count"], "opts": "facts2", "rule": "adj1cc3",
           "facts": ["pos", "gap", "medge", "moves", "touch"], "note": "t1 with rule adj1cc3"},
    "t3n": {"template": "noopts", "state": ["count"], "opts": "facts2", "rule": "adj1cc3",
            "facts": ["pos", "gap", "medge", "moves", "touch"], "note": "t3, options only as criteria (not listed in the prompt)"},
    "t4": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "adj1cc3",
           "facts": ["pos", "gap", "medge", "anchors", "touch"],
           "note": "t3n + state 'Lines so far' (runs named by where they lie, no coords) + option anchors naming those lines"},
    "t5": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "all",
           "facts": ["pos", "gap2", "medge", "anchors", "touch"],
           "note": "t4 + corner-touch names the corner + gap>=2 says 'more than one street' + every free cell"},
    "t5r": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "adj1cc3",
            "facts": ["pos", "gap2", "medge", "anchors", "touch"], "note": "t5 with rule adj1cc3"},
    "t6": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "all",
           "facts": ["pos", "gap2", "medge", "anchors2", "touch"],
           "note": "t5 + anchors name lines without their gap, single houses with their map corner"},
    "t7": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "touch",
           "facts": ["pos", "gap2", "medge", "anchors2", "touch"], "note": "t6 with rule touch"},
    "t8": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "all",
           "facts": ["pos", "gap2", "medge", "anchors3", "touch"], "note": "t6 without the new line length in options"},
    "t9": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "all",
           "facts": ["pos", "gap2", "medge", "amain"],
           "note": "t8 with only the strongest line relation per option (no 'off the middle'), touch fact only when alone"},
    "t10": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "all",
            "facts": ["pos2", "gap2", "medge", "amain4"],
            "note": "t9 + face position ('north of the CC, slightly east' / 'diagonally off its NE corner'), no 'making a corner', a single house 'starts a row'"},
    "t10nc": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "all", "coords": False,
              "facts": ["pos2", "gap2", "medge", "amain4"], "note": "t10 without coordinates in option texts"},
    "t11": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "all", "coords": False,
            "facts": ["pos3", "gap2", "medge", "amain4"],
            "note": "t10nc with pos3: no 'diagonally' in positions, straight-on cells name the half they face"},
    "t12": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "all", "coords": False,
            "facts": ["pos3", "gap2", "medge", "amain4"], "strict_runs": True,
            "note": "t11 + a 2-house line whose both houses are already in longer lines is not reported"},
    # -- round 3: how the state is passed (Josue's hypothesis) --
    "s_count": {"template": "noopts", "state": ["count"], "opts": "facts2", "rule": "all", "coords": False,
                "facts": ["pos3", "gap2", "medge", "amain4"], "strict_runs": True,
                "note": "t12 with state = house count only (control)"},
    "s_event": {"template": "noopts", "state": ["count", "event"], "opts": "facts2", "rule": "all", "coords": False,
                "facts": ["pos3", "gap2", "medge", "amain4"], "strict_runs": True,
                "note": "t12 with state = count + what the last house changed (no list of lines)"},
    "t13": {"template": "noopts", "state": ["count", "lines", "event"], "opts": "facts2", "rule": "all",
            "coords": False, "facts": ["pos3", "gap2", "medge", "amain4"], "strict_runs": True,
            "note": "t12 + 'Last house: …' event line (what the last answer changed)"},
    "t14": {"template": "noopts", "state": ["count", "lines", "event_where"], "opts": "facts2", "rule": "all",
            "coords": False, "facts": ["pos3", "gap2", "medge", "amain4"], "strict_runs": True,
            "note": "t12 + 'Last house:' where it is and which line end it now is (no action verb, no corner event)"},
    "t15": {"template": "noopts", "state": ["count", "lines", "event_corner"], "opts": "facts2", "rule": "all",
            "coords": False, "facts": ["pos3", "gap2", "medge", "amain4"], "strict_runs": True,
            "note": "t14 + the corner event ('the last house turned a corner at the east end of …')"},
    "t16": {"template": "noopts", "state": ["count", "lines"], "opts": "facts2", "rule": "all", "coords": False,
            "facts": ["pos3", "gap2", "medge", "amain4"], "strict_runs": True, "axis_names": True,
            "note": "t12 + a north-south line is named by its west/east side of the CC (was: north/south for a corner stub)"},
    "t17": {"template": "noopts", "state": ["count", "lines", "event_corner"], "opts": "facts2", "rule": "all",
            "coords": False, "facts": ["pos3", "gap2", "medge", "amain4"], "strict_runs": True, "axis_names": True,
            "note": "t16 + 'Last house:' where it is, which line end it is, the corner event (t15 with axis names)"},
    "nl_nocoords": {"template": "base", "state": ["count"], "opts": "facts", "rule": "reach2", "coords": False,
                    "facts": ["gap", "touch", "edge"], "note": "nl_count, no coordinates in option texts"},
}


def build(variant: str, goal_text: str, houses: list, n: int, total: int):
    v = VARIANTS[variant]
    RUNS_STRICT["on"] = bool(v.get("strict_runs"))
    AXIS_NAMES["on"] = bool(v.get("axis_names"))
    POS["fn"] = (pos2_phrase if "pos2" in v.get("facts", []) else pos3_phrase if "pos3" in v.get("facts", [])
                 else None)
    cells = RULES[v["rule"]][1](houses)
    options = OPTION_STYLES[v["opts"]](cells, houses, v)
    state = "\n".join(STATE_STYLES[s](houses) for s in v["state"])
    prompt = TEMPLATES[v["template"]].format(
        goal=goal_text, xmax=GRID - 1, ymax=GRID - 1, cc=", ".join(cell_txt(c) for c in CC),
        state=state, n=n, total=total, options="\n".join(f"- {o['id']}: {o['text']}" for o in options))
    return prompt, options


# == lab client (same pattern as mcp_server._post) ============================================
def ask(prompt: str, options: list) -> dict:
    body = {"run": CARRIER["run"], "idx": CARRIER["idx"], "model": "jev", "prompt": prompt,
            "questions": {CARRIER["qkey"]: {"type": "choice", "instructions": INSTRUCTIONS,
                                            "criteria": {o["id"]: o["text"] for o in options}}}}
    req = urllib.request.Request(LAB + "/api/ask", data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Lab": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 -- localhost only; a hung ask is retried once
            d = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            d = json.loads(exc.read())
        except (ValueError, OSError):
            d = {"ok": False, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "error": f"lab not reachable at {LAB}: {exc}"}
    r = d.get("result") or {}
    if not d.get("ok") or not r.get("ok"):
        return {"ok": False, "error": r.get("error") or d.get("error") or "no answer", "ms": r.get("ms")}
    if r.get("choice") not in {o["id"] for o in options}:
        return {"ok": False, "error": f"choice {r.get('choice')!r} is not an option", "ms": r.get("ms"),
                "raw": r.get("raw")}
    return r


# == scoring (the only place that knows the target) ===========================================
def _ring(lo, hi):
    return {(x, y) for x in range(lo, hi + 1) for y in range(lo, hi + 1) if x in (lo, hi) or y in (lo, hi)}


TARGETS = {
    "square6": _ring(2, 7),
    "square4": _ring(3, 6),
    "square8": _ring(1, 8),
    "triangle": {(x, 2) for x in range(2, 8)} | {(2, y) for y in range(2, 8)} | {(6, 3), (3, 6)},
    "triangle_se": ({(x, 9) for x in range(4, 10)} | {(9, y) for y in range(4, 10)}
                    | {(5, 8), (6, 7), (7, 6), (8, 5)}),
}


def target_of(goal: str) -> list:
    return sorted(TARGETS[goal], key=lambda c: (c[1], c[0]))


def score_options(goal: str, options: list):
    t = set(target_of(goal))
    on = sum(o["cell"] in t for o in options)
    return on, len(options) - on


# == run =====================================================================================
def save(run: dict):
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
    index.append({"id": run["id"], "mode": run["mode"], "variant": run["variant"],
                  "goal": run.get("goal", "square6"), "tier": run.get("tier", "A"),
                  "total": len(run["target"]), "started": run["started"], **run["summary"]})
    index.sort(key=lambda e: e["id"], reverse=True)
    tmp = idx_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=1))
    tmp.replace(idx_path)


def ask_retry(prompt, options, n):
    r = ask(prompt, options)
    if not r.get("ok"):   # transport error only (not a wrong answer)
        print(f"  q{n}: {r.get('error')} -- retrying once", flush=True)
        r = ask(prompt, options)
    return r


def one_run(variant: str, goal: str, steps: int) -> dict:
    v = VARIANTS[variant]
    g = GOALS[goal]
    target = target_of(goal)
    tset = set(target)
    total = g["houses"]
    assert total == len(target), f"goal {goal}: {total} houses but target has {len(target)} cells"
    now = datetime.now()
    run = {"id": f"{now:%Y%m%d-%H%M%S}-{variant}-{goal}", "mode": v["opts"], "variant": variant,
           "goal": goal, "tier": g["tier"], "goal_text": g["text"], "variant_spec": v,
           "rule": RULES[v["rule"]][0], "started": now.isoformat(timespec="seconds"),
           "prompt_template": TEMPLATES[v["template"]], "instructions": INSTRUCTIONS, "model": "jev",
           "grid": {"w": GRID, "h": GRID}, "cc": CC, "target": target, "steps": [],
           "summary": {"on_target": 0, "off_target": 0, "steps": 0}}
    houses: list = []
    for n in range(1, steps + 1):
        prompt, options = build(variant, g["text"], houses, n, total)
        if not options:
            run["summary"]["error"] = "no free cell left"
            break
        n_on, n_off = score_options(goal, options)
        t0 = time.time()
        r = ask_retry(prompt, options, n)
        if not r.get("ok"):
            run["summary"]["error"] = f"q{n}: {r.get('error')}"
            run["steps"].append({"n": n, "prompt": prompt, "options": [o["id"] for o in options],
                                 "error": r.get("error"), "raw": r.get("raw")})
            save(run)
            print(f"stopped: {run['summary']['error']}", flush=True)
            break
        byid = {o["id"]: o for o in options}
        cell = byid[r["choice"]]["cell"]
        on = cell in tset
        houses.append(cell)
        probs = r.get("probabilities") or {}
        top = sorted(probs, key=lambda k: -probs[k])[:5]
        p_on = round(sum(p for k, p in probs.items() if k in byid and byid[k]["cell"] in tset), 3)
        run["steps"].append({"p_on": p_on,
            "n": n, "prompt": prompt, "options": [o["id"] for o in options],
            "cells": {o["id"]: o["cell"] for o in options}, "n_on": n_on, "n_off": n_off,
            "top": [{"id": k, "p": probs[k], "text": byid[k]["text"], "on": byid[k]["cell"] in tset}
                    for k in top if k in byid],
            "probabilities": probs, "choice": r["choice"], "cell": cell, "p": probs.get(r["choice"]),
            "ms": r.get("ms") or round((time.time() - t0) * 1000), "on_target": on, "raw": r.get("raw")})
        s = run["summary"]
        s["on_target" if on else "off_target"] += 1
        s["steps"] = n
        if not on and "first_off" not in s:
            s["first_off"] = n
        save(run)
        p = probs.get(r["choice"])
        if VERBOSE:
            print(f"q{n:2d}: {len(options):2d} opts ({n_on} on) -> {r['choice']:6s} "
                  f"p={p if p is None else round(p, 2)} P(on)={p_on:.2f} {'on ' if on else 'OFF'}", flush=True)
    ms = [st["ms"] for st in run["steps"] if st.get("ms")]
    run["summary"]["avg_ms"] = round(sum(ms) / len(ms)) if ms else None
    pons = [st["p_on"] for st in run["steps"] if "p_on" in st]
    if pons:
        run["summary"]["p_on_min"] = min(pons)
        run["summary"]["p_on_mean"] = round(sum(pons) / len(pons), 3)
    save(run)
    s = run["summary"]
    print(f"run {run['id']}: on target {s['on_target']}/{len(target)}, off {s['off_target']}, "
          f"first off q{s.get('first_off', '-')}, P(on) min {s.get('p_on_min')} mean {s.get('p_on_mean')}" + (f", error: {s['error']}" if s.get("error") else ""))
    return run


# map states for single-question probes (test fixtures, scoring side; the builder never sees them)
PROBES = {
    "square6": {
        "empty": "",
        "row5": "2,2 3,2 4,2 5,2 6,2",
        "side1": "2,2 3,2 4,2 5,2 6,2 7,2",
        "side2": "2,2 3,2 4,2 5,2 6,2 7,2 7,3 7,4 7,5 7,6 7,7",
        "side3": "2,2 3,2 4,2 5,2 6,2 7,2 7,3 7,4 7,5 7,6 7,7 6,7 5,7 4,7 3,7 2,7",
        "mid3": "4,2 5,2 6,2",
    },
    "triangle": {
        "empty": "",
        "corner": "2,2",
        "northleg": "2,2 3,2 4,2 5,2 6,2 7,2",
        "northleg_d": "2,2 3,2 4,2 5,2 6,2 7,2 6,3",
        "legs": "2,2 3,2 4,2 5,2 6,2 7,2 2,3 2,4 2,5 2,6 2,7",
        "legs_d": "2,2 3,2 4,2 5,2 6,2 7,2 2,3 2,4 2,5 2,6 2,7 6,3",
    },
    "triangle_se": {
        "empty": "",
        "corner": "9,9",
        "bottom3": "9,9 8,9 7,9",
        "bottomleg": "9,9 8,9 7,9 6,9 5,9 4,9",
        "legs": "9,9 8,9 7,9 6,9 5,9 4,9 9,8 9,7 9,6 9,5 9,4",
        "legs_d": "9,9 8,9 7,9 6,9 5,9 4,9 9,8 9,7 9,6 9,5 9,4 5,8",
        "diag2": "6,7 7,6",
    },
}


def probe_set(variant: str, goal: str, quiet=False):
    """One question per PROBES map; returns (on, total)."""
    on = 0
    for name, hs in PROBES[goal].items():
        houses = [tuple(int(v) for v in p.split(",")) for p in hs.split()]
        if not quiet:
            print(f"--- {name}")
        ok = probe(variant, goal, houses, k=0 if quiet else 3, label=name)
        on += bool(ok)
    return on, len(PROBES[goal])


def probe(variant: str, goal: str, houses: list, k: int = 6, label=""):
    g = GOALS[goal]
    tset = set(target_of(goal))
    prompt, options = build(variant, g["text"], houses, len(houses) + 1, g["houses"])
    r = ask_retry(prompt, options, len(houses) + 1)
    if not r.get("ok"):
        print("error:", r.get("error"))
        return None
    probs = r.get("probabilities") or {}
    byid = {o["id"]: o for o in options}
    n_on, _ = score_options(goal, options)
    p_on = sum(p for i, p in probs.items() if i in byid and byid[i]["cell"] in tset)
    on = byid[r["choice"]]["cell"] in tset
    best_off = max((i for i in probs if i in byid and byid[i]["cell"] not in tset), key=lambda i: probs[i], default=None)
    print(f"[{variant}/{goal}] {label:11s} h={len(houses):2d} opts={len(options):2d} ({n_on:2d} on) "
          f"choice={r['choice']:5s} {'on ' if on else 'OFF'} P(on)={p_on:.2f}"
          + ("" if on or best_off is None else f"  <- {probs[best_off]:.2f} {byid[best_off]['text'][:140]}"))
    for i in sorted(probs, key=lambda x: -probs[x])[:k]:
        print(f"  {probs[i]:.2f} {'on ' if byid[i]['cell'] in tset else 'OFF'} {byid[i]['text'][:170]}")
    return on


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="facts")
    ap.add_argument("--goal", choices=sorted(GOALS), default="square6")
    ap.add_argument("--steps", type=int, default=0, help="default: the goal's house count")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--probe", default=None, help='houses already built, e.g. "2,2 3,2"; asks one question')
    ap.add_argument("--probeset", action="store_true", help="one question on each PROBES map")
    ap.add_argument("--probeall", action="store_true", help="--probeset for square6 + both triangles")
    ap.add_argument("--print-prompt", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="one line per question")
    args = ap.parse_args(argv)
    global VERBOSE
    VERBOSE = args.verbose
    if args.probeset:
        probe_set(args.variant, args.goal)
        return 0
    if args.probeall:
        tot = [probe_set(args.variant, g, quiet=True) for g in ("square6", "triangle", "triangle_se")]
        print(f"probes on target: {sum(a for a, _ in tot)}/{sum(b for _, b in tot)}  "
              + "  ".join(f"{a}/{b}" for a, b in tot))
        return 0
    if args.probe is not None:
        houses = [tuple(int(v) for v in p.split(",")) for p in args.probe.split()]
        if args.print_prompt:
            print(build(args.variant, GOALS[args.goal]["text"], houses, len(houses) + 1,
                        GOALS[args.goal]["houses"])[0])
        probe(args.variant, args.goal, houses)
        return 0
    scores = []
    for _ in range(args.runs):
        run = one_run(args.variant, args.goal, args.steps or GOALS[args.goal]["houses"])
        scores.append(f"{run['summary']['on_target']}/{len(run['target'])} ({run['id']})")
    print("scores:", ", ".join(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())
