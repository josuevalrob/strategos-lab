"""The 3D map: the same graph as the 2D map, split into two flows on two planes.

* READS plane (z > 0): what the model reads -- civ file, hero files, game state,
  opponent read -> the prompt (render_play) -> the ``play`` node.
* CAN-PICK plane (z < 0): what the model can pick -- triggers -> parts (why asked)
  -> play options -> Petra action -> Petra managers / queues.
* The ``play`` node sits between the planes (z = 0): the two flows meet there.

x = the column (left to right), y = the spread within a column, z = the plane.
Positions are fixed here; the page pins them (fx/fy/fz) and never runs a force layout.
"""

from __future__ import annotations

from . import code, config, mapgraph

COL_X = 280.0
ROW_Y = 42.0
PLANE_Z = {"reads": 240.0, "both": 0.0, "pick": -240.0}
PLANES = [
    {"id": "reads", "label": "What the model READS", "z": PLANE_Z["reads"]},
    {"id": "pick", "label": "What the model CAN PICK", "z": PLANE_Z["pick"]},
]
COLUMNS = {
    "reads": ["Inputs", "Prompt", "play"],
    "pick": ["Triggers", "Why asked (parts)", "play options", "Petra action", "Petra managers / queues"],
}
LEGEND = [
    {"kind": "reads", "label": "prompt (reads)", "dashed": False},
    {"kind": "picks", "label": "play offers", "dashed": False},
    {"kind": "offers", "label": "trigger / part", "dashed": False},
    {"kind": "petra", "label": "to Petra", "dashed": False},
    {"kind": "describes", "label": "hero file describes", "dashed": True},
    {"kind": "config", "label": "switches on / event", "dashed": True},
]

GROUP_PLACE = {"input": ("reads", 0), "part": ("pick", 1), "option": ("pick", 2),
               "action": ("pick", 3), "manager": ("pick", 4), "queue": ("pick", 4)}

TRIGGER_LABELS = {
    "pass_order": "enemy at the pass, new read,\nnew playbook, phase, rule",
    "play_order": "same events,\nno pass on the map",
    "hero_next": "hero slot open (City,\nbuilding, nobody in field)",
    "tower_site": "Town reached /\nenemy near the gate",
    "garrison_now": "enemy within a\ntower's range",
    "wall_now": "walls possible, hero lost,\narmy behind",
    "petra": "Petra could do it now",
}


def _edge_kind(src: str, dst: str, style: str) -> str:
    if style == "dashed":
        return "describes" if src.startswith("in:hero:") and dst.startswith("opt:") else "config"
    if dst in ("prompt", "q:play"):
        return "reads"
    if src == "q:play":
        return "picks"
    if src.startswith(("trig:", "part:")):
        return "offers"
    return "petra"


def build(cfg: config.Config, civ: str = "spart") -> dict:
    g = mapgraph.build(cfg, civ)
    facts = code.load(cfg)
    nodes: list[dict] = []
    for n in g["nodes"]:
        m = {k: v for k, v in n.items() if k not in ("parent", "col")}
        if n["id"] == "q:play":
            m["plane"], m["col"] = "both", 2
            m["label"] = "play\n(play.json)"
        else:
            m["plane"], m["col"] = GROUP_PLACE[n["group"]]
        nodes.append(m)
    ids = {n["id"] for n in nodes}

    # The prompt: what render_play puts together (the READS flow's middle).
    nodes.append({"id": "prompt", "label": "prompt\n(render_play)", "group": "prompt", "plane": "reads",
                  "col": 1, "details": {
                      "title": "The prompt the model reads (advisor_adapters.render_play)",
                      "summary": ["'We are player N.'",
                                  "the civ file as written (params.heroOrder stripped since 12d-2)",
                                  "every hero file of the civ, as written",
                                  "'Now:' the state lines of the parts asked this turn + our stock",
                                  "then play.json: the instructions + the criteria of the options offered"],
                      "anchors": [facts.anchor(a) for a in ("render_play", "question_spec",
                                                            "jev_build_prompt", "jev_post")]}})
    # The triggers: what makes each part ask (the CAN-PICK flow's first column).
    parts = {p["id"]: p for p in code.PARTS}
    for n in list(nodes):
        if n["group"] != "part":
            continue
        pid = n["id"].split(":", 1)[1]
        part = parts.get(pid, {})
        nodes.append({"id": f"trig:{pid}", "label": TRIGGER_LABELS.get(pid, pid), "group": "trigger",
                      "plane": "pick", "col": 0, "dim": n.get("dim", False), "details": {
                          "title": f"Trigger of {pid}", "trigger": part.get("trigger"),
                          "options_text": part.get("options"), "rule": part.get("rule"),
                          "who": part.get("who"),
                          "anchors": [facts.anchor(a) for a in part.get("anchors", [])]}})
    ids = {n["id"] for n in nodes}

    edges: list[dict] = []

    def add(src, dst, label, style="solid"):
        if src in ids and dst in ids:
            eid = f"{src}->{dst}"
            if not any(e["id"] == eid for e in edges):
                edges.append({"id": eid, "source": src, "target": dst, "label": label,
                              "kind": _edge_kind(src, dst, style), "dashed": style == "dashed"})

    for e in g["edges"]:
        if e["target"] == "q:play":
            add(e["source"], "prompt", e["label"] or "prompt", e["style"])   # inputs feed the prompt
        else:
            add(e["source"], e["target"], e["label"], e["style"])
    add("prompt", "q:play", "the model reads")
    for n in nodes:
        if n["group"] == "option":
            add("q:play", n["id"], "can pick")
        if n["group"] == "trigger":
            add(n["id"], "part:" + n["id"].split(":", 1)[1], "fires")

    # Positions: rows centred per (plane, column); play in the middle, between the planes.
    groups: dict[tuple, list] = {}
    for n in nodes:
        groups.setdefault((n["plane"], n["col"]), []).append(n)
    for (plane, col), members in groups.items():
        k = len(members)
        for i, n in enumerate(members):
            n["row"] = i
            n["pos"] = {"x": (col - 2) * COL_X, "y": round((k - 1) / 2 * ROW_Y - i * ROW_Y, 2),
                        "z": PLANE_Z[plane]}

    planes = []
    for p in PLANES:
        on = [n for n in nodes if n["plane"] == p["id"]]
        ys = [n["pos"]["y"] for n in on] or [0.0]
        cols = sorted({n["col"] for n in on} | ({2} if p["id"] == "reads" else set()))
        planes.append({**p, "x0": (min(cols) - 2) * COL_X - COL_X / 2,
                       "x1": (max(cols) - 2) * COL_X + COL_X / 2,
                       "y0": min(ys) - ROW_Y * 1.5, "y1": max(ys) + ROW_Y * 3})
    columns = [{"plane": p, "col": i, "x": (i - 2) * COL_X, "label": label}
               for p, labels in COLUMNS.items() for i, label in enumerate(labels)]
    return {"civ": civ, "nodes": nodes, "edges": edges, "planes": planes, "columns": columns,
            "legend": LEGEND, "warnings": g["warnings"], "code_errors": g["code_errors"],
            "civs": g["civs"]}
