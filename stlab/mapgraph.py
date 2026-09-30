"""L1: the map of one civ -- inputs -> parts -> play options -> Petra action -> managers."""

from __future__ import annotations

from . import code, config, qa

COLS = ["Inputs", "Why asked (parts)", "play options (the model picks one)", "Petra action",
        "Petra managers / queues"]

# Which civ-file params block switches a part on (a part without it is never asked).
PART_BLOCK = {"hero_next": "heroOrder", "tower_site": "towerSite", "garrison_now": "garrisonNow",
              "wall_now": "decline"}


def criteria_text(criteria: dict, option: str) -> tuple[str | None, str | None]:
    """(text, key used) the way question_spec resolves it (criteria_by_prefix)."""
    if option in criteria:
        return criteria[option], option
    head, sep, param = option.partition(":")
    if sep and head + ":" in criteria:
        return criteria[head + ":"].replace("{param}", param.replace("_", " ")), head + ":"
    return None, None


def _anchors(facts: code.CodeFacts, ids) -> list[dict]:
    return [facts.anchor(a) for a in ids]


def build(cfg: config.Config, civ: str = "spart") -> dict:
    facts = code.load(cfg)
    files = qa.list_files(cfg)
    civ_rel = qa.civ_path(civ)
    civ_doc = qa.read(cfg, civ_rel)
    civ_data = civ_doc.get("data") or {}
    params = civ_data.get("params") or {}
    hero_files = []
    for h in files["heroes"].get(civ, []):
        doc = qa.read(cfg, h["path"])
        hero_files.append({"name": h["name"], "path": h["path"], "data": doc.get("data") or {},
                           "error": doc.get("error")})
    play_rel = qa.question_path("play")
    play_doc = qa.read(cfg, play_rel)
    play_q = ((play_doc.get("data") or {}).get("questions") or {}).get("play") or {}
    criteria = play_q.get("criteria") or {}
    strat_defs = qa.stratagems(cfg)
    doc_text = cfg.path(config.DOCTRINE_JSON).read_text(encoding="utf-8") \
        if cfg.path(config.DOCTRINE_JSON).exists() else ""
    try:
        _dv, doc_spans = qa.parse_spans(doc_text) if doc_text else (None, {})
    except (ValueError, IndexError):
        doc_spans = {}

    def doctrine_line(kind: str) -> int | None:
        span = doc_spans.get(("stratagems", kind, "orders"))
        return doc_text.count("\n", 0, span[0]) + 1 if span else None

    def doctrine_source(kind: str) -> dict:
        return {"path": config.DOCTRINE_JSON, "focus": f"stratagem:{kind}", "line": doctrine_line(kind),
                "label": f"doctrine.json:{doctrine_line(kind)} stratagems.{kind}.orders"}

    nodes, edges, warnings = [], [], []
    ids = set()

    def node(nid, label, col, group, details, parent=None, **extra):
        if nid in ids:
            return
        ids.add(nid)
        n = {"id": nid, "label": label, "col": col, "group": group, "details": details}
        if parent:
            n["parent"] = parent
        n.update(extra)
        nodes.append(n)

    def edge(src, dst, label="", style="solid"):
        eid = f"{src}->{dst}"
        if src in ids and dst in ids and not any(e["id"] == eid for e in edges):
            edges.append({"id": eid, "source": src, "target": dst, "label": label, "style": style})

    # -- column 0: inputs ---------------------------------------------------------
    civ_lines = [str(x.get("text") if isinstance(x, dict) else x) for x in civ_data.get("text") or []]
    node("in:civ", f"civ file\n{civ}.json", 0, "input", {
        "title": f"Civ file: {civ_rel}", "file": civ_rel, "edit": civ_rel,
        "summary": [f"name: {civ_data.get('name')}",
                    f"heroes (prompt order): {', '.join(civ_data.get('heroes') or []) or '-'}",
                    f"params.doctrine: {params.get('doctrine')}",
                    f"params.withoutChoke: {params.get('withoutChoke')}",
                    "params.heroOrder: code's fallback only; stripped from the prompt (12d-2)"],
        "text_lines": civ_lines, "error": civ_doc.get("error"),
        "anchors": _anchors(facts, ["render_play"])})
    for h in hero_files:
        d = h["data"]
        node(f"in:hero:{h['name']}", f"hero file\n{h['name']}.json", 0, "input", {
            "title": f"Hero file: {h['path']}", "file": h["path"], "edit": h["path"],
            "summary": [f"hero: {d.get('hero')}", f"templates: {', '.join(d.get('templates') or [])}",
                        f"battle: {d.get('battle')}",
                        f"params.playbook: {(d.get('params') or {}).get('playbook')}"],
            "text_lines": [str(x.get("text") if isinstance(x, dict) else x) for x in d.get("text") or []],
            "error": h["error"], "anchors": _anchors(facts, ["render_play"])})
    keys = facts.play_state_keys
    node("in:state", "game state\n(\"Now:\" lines)", 0, "input", {
        "title": "Game state the head sends with the play question (playState)",
        "summary": ["game_minute, our_phase, food/wood/stone/metal, pop, pop_max, events",
                    "plus, from the parts asked this turn: " + ", ".join(keys)],
        "anchors": _anchors(facts, ["play_state", "play_state_keep", "render_play"])})
    node("in:opponent", "opponent read\n(opponent_class)", 0, "input", {
        "title": "opponent_class: asked every decision tick (its own question)",
        "summary": ["options: " + ", ".join(facts.classes),
                    "rule: rules.classifyV2; the model's last answer is the read the next tick acts on",
                    "feeds the play question: features.opponent, and a new read is an event of the "
                    "pass order and hero_next parts"],
        "anchors": _anchors(facts, ["opponent_ask", "decide"])})

    # -- column 1: parts --------------------------------------------------------
    civ_strats = qa.civ_stratagems(civ_data, hero_files)
    pass_kinds = [s for s in civ_strats if (strat_defs.get(s["kind"]) or {}).get("needsChoke")]
    open_kinds = [s for s in civ_strats if s["kind"] in strat_defs and
                  not strat_defs[s["kind"]].get("needsChoke")]
    for s in civ_strats:
        if s["kind"] not in strat_defs:
            warnings.append(f"stratagem {s['kind']!r} ({s['why']}) is not in doctrine.json")
    for part in code.PARTS:
        pid = part["id"]
        present = True
        note = ""
        if pid == "pass_order":
            present = bool(pass_kinds)
            note = "stratagems: " + ", ".join(f"{s['kind']} [{s['why']}]" for s in pass_kinds)
        elif pid == "play_order":
            present = bool(open_kinds)
            note = "stratagems: " + ", ".join(f"{s['kind']} [{s['why']}]" for s in open_kinds)
        elif pid in PART_BLOCK:
            present = PART_BLOCK[pid] in params
            note = f"switched on by params.{PART_BLOCK[pid]}" + ("" if present else " (missing: never asked)")
        if not present and pid in ("pass_order", "play_order"):
            continue
        qf = part.get("question_file")
        pdet = {"title": f"Part: {pid}", "trigger": part["trigger"], "options_text": part["options"],
                "rule": part["rule"], "who": part["who"], "note": note,
                "question_file": qa.question_path(qf[:-5]) if qf else None,
                "anchors": _anchors(facts, part["anchors"] + ["offer", "play_token"])}
        if pid in ("pass_order", "play_order"):
            pdet["sources"] = [doctrine_source(k["kind"]) for k in
                               (pass_kinds if pid == "pass_order" else open_kinds)]
        node(f"part:{pid}", part["label"], 1, "part", pdet, dim=not present)

    node("q:play", "play  (play.json)", 2, "question", {
        "title": "The one question: play (source/tools/strategos/questions/play.json)",
        "file": play_rel, "edit": play_rel,
        "instructions": play_q.get("instructions"), "criteria": criteria,
        "summary": ["Pending parts merge into ONE question per turn; every open option of every part, "
                    "Petra's own orders, and economy.",
                    "The first part's rule is the default: no answer by the deadline = the rule's pick.",
                    "Prompt: 'We are player N.' + civ file (heroOrder stripped) + every hero file + "
                    "'Now:' state lines; wording = instructions + criteria of the options offered.",
                    "Sent to Jev as response_format {type: questions}; answer -> strategos-hint -> "
                    "desk -> applyPlayAnswer."],
        "anchors": _anchors(facts, ["ask_play", "ask_play_desk", "play_rule", "render_play",
                                    "question_spec", "jev_build_prompt", "jev_post", "answer_body",
                                    "desk_resolve", "desk_by_rule", "apply_play_answer"])})

    # -- column 2: options ------------------------------------------------------
    options = []   # (token, part ids, action ids)

    def option(token, parts, actions, extra_summary=None, bad=None, sources=None, label=None):
        text, key = criteria_text(criteria, token)
        summ = [f"criteria ({key or 'none'}): {text if text is not None else 'NO WORDING'}"]
        if text is None:
            summ.append("No criteria text for this option: question_spec falls back to a GENERIC "
                        "question for any turn that offers it.")
            warnings.append(f"play.json has no criteria for option {token!r}")
        if extra_summary:
            summ += extra_summary
        details = {"title": f"Option: {token}", "criteria_key": key, "criteria_text": text,
                   "edit": play_rel, "summary": summ}
        if sources:
            details["sources"] = sources
        node(f"opt:{token}", label or token, 2, "option", details, parent="q:play", bad=bool(bad))
        options.append((token, parts, actions))

    option("economy", [], ["act:economy"], ["Always offered (askPlay adds it last)."])
    # The military options: each stratagem's order list in doctrine.json (one node per
    # order, listing every stratagem of this civ that has it).
    mil: dict[str, dict] = {}
    for s in civ_strats:
        d = strat_defs.get(s["kind"])
        if not d:
            continue
        pid = "part:pass_order" if d.get("needsChoke") else "part:play_order"
        for order in d.get("orders") or []:
            m = mil.setdefault(order, {"parts": [], "actions": [], "summary": [], "sources": []})
            if pid not in m["parts"]:
                m["parts"].append(pid)
            m["actions"].append(f"act:{s['kind']}/{order}")
            m["summary"].append(f"playbook order of {s['kind']} ({s['why']}): doctrine.json "
                                f"stratagems.{s['kind']}.orders, line {doctrine_line(s['kind'])}")
            m["sources"].append(doctrine_source(s["kind"]))
    for order, m in mil.items():
        option(order, m["parts"], m["actions"], m["summary"], sources=m["sources"],
               label=f"{order}  (doctrine.json)")
    hero_order = (params.get("heroOrder") or {}).get("order") or []
    for t in hero_order:
        tok = "hero:" + qa.hero_option(t)
        exists = qa.template_exists(cfg, t, civ)
        option(tok, ["part:hero_next"], ["act:hero/train"],
               [f"template {t}: " + (exists or "NO TEMPLATE FOUND")], bad=not exists)
    for site in facts.tower_sites:
        option(f"tower:{site}", ["part:tower_site"], ["act:towers/tower"])
    option("garrison:<tower>", ["part:garrison_now"], ["act:guard/garrison"],
           ["The tower id is filled in per question (garrison:3953)."])
    option("wall", ["part:wall_now"], ["act:walls/wall"])
    for tok in ("advance", "train:workers", "train:soldiers"):
        option(tok, ["part:petra"], [f"act:{tok}"])

    # -- column 3/4: actions and managers -----------------------------------------
    def manager(name, group="manager"):
        node(f"mgr:{name}", name, 4, group, {
            "title": f"Petra: {name}",
            "summary": ["A Headquarters-owned manager, resolved by name at dispatch (head.js route)."
                        if group == "manager" else "One of Petra's (or Strategos') queues."],
            "anchors": _anchors(facts, ["order_routes", "route"])})

    def routed_action(aid, stratagem, order, label, queues=(), anchors=()):
        allowed = facts.stratagem_orders.get(stratagem)
        routes = facts.order_routes.get(order)
        problems = []
        if allowed is None:
            problems.append(f"head.js has no stratagem {stratagem!r} (STRATAGEM_ORDERS): dispatch ignores it")
        elif order not in allowed:
            problems.append(f"STRATAGEM_ORDERS[{stratagem!r}] = {allowed}: order {order!r} is ignored")
        if routes is None:
            problems.append(f"ORDER_ROUTES has no {order!r}")
        elif not routes and order not in facts.managerless:
            problems.append(f"order {order!r} routes to no manager")
        summ = [f"hint {{stratagem: {stratagem}, order: {order}}} -> dispatch -> route",
                f"ORDER_ROUTES[{order!r}] = {routes} (head.js:{facts.order_lines.get(order)})",
                f"STRATAGEM_ORDERS[{stratagem!r}] = {allowed} "
                f"(head.js:{facts.stratagem_lines.get(stratagem)})"]
        node(aid, label, 3, "action", {"title": f"Petra action: {label}", "summary": summ + problems,
                                       "anchors": _anchors(facts, list(anchors) + ["dispatch",
                                                                                  "dispatch_allowed",
                                                                                  "route"])},
             bad=bool(problems))
        for m in routes or []:
            manager(m)
            edge(aid, f"mgr:{m}", "routes to")
        for qn in queues:
            manager(qn, "queue")
            edge(aid, f"mgr:{qn}", "queue")
        return problems

    node("act:economy", "no order\n(Petra's economy)", 3, "action", {
        "title": "economy: no order sent",
        "summary": [code.DIRECT_ACTIONS["economy"]["label"]],
        "anchors": _anchors(facts, code.DIRECT_ACTIONS["economy"]["anchors"])})
    for s in civ_strats:
        d = strat_defs.get(s["kind"])
        if not d:
            continue
        for order in d.get("orders") or []:
            probs = routed_action(f"act:{s['kind']}/{order}", s["kind"], order,
                                  f"{s['kind']}/{order}", anchors=["apply_play", "apply_play_dedup"])
            warnings += [f"{s['kind']}/{order}: {p}" for p in probs]
    for prefix in ("hero:", "tower:", "garrison:", "wall"):
        da = code.DIRECT_ACTIONS[prefix]
        routed_action(f"act:{da['stratagem']}/{da['order']}", da["stratagem"], da["order"],
                      f"{da['stratagem']}/{da['order']}\n{da['label']}", da["queues"], da["anchors"])
    for tok in ("advance", "train:workers", "train:soldiers"):
        da = code.DIRECT_ACTIONS[tok]
        node(f"act:{tok}", da["label"].replace(": ", ":\n", 1), 3, "action", {
            "title": f"Petra's own order: {tok}", "summary": [da["label"],
                                                             "Carried out by the head itself "
                                                             "(petraAction), as Petra's own code does."],
            "anchors": _anchors(facts, da["anchors"])})
        for qn in da["queues"]:
            manager(qn, "queue")
            edge(f"act:{tok}", f"mgr:{qn}", "queue")

    # -- edges ------------------------------------------------------------------
    edge("in:civ", "q:play", "prompt")
    for h in hero_files:
        edge(f"in:hero:{h['name']}", "q:play", "prompt")
        for t in (h["data"].get("templates") or []):
            edge(f"in:hero:{h['name']}", f"opt:hero:{qa.hero_option(t)}", "describes", "dashed")
    edge("in:state", "q:play", "prompt")
    edge("in:opponent", "part:pass_order", "event", "dashed")
    edge("in:opponent", "part:play_order", "event", "dashed")
    edge("in:opponent", "part:hero_next", "event", "dashed")
    edge("in:opponent", "in:state", "features.opponent", "dashed")
    for pid, block in PART_BLOCK.items():
        edge("in:civ", f"part:{pid}", f"params.{block}", "dashed")
    edge("in:civ", "part:pass_order", "params.doctrine", "dashed")
    edge("in:civ", "part:play_order", "params.withoutChoke", "dashed")
    for token, parts, actions in options:
        for p in parts:
            edge(p, f"opt:{token}", "offers")
        for a in actions:
            edge(f"opt:{token}", a, "")

    # What cannot work for this civ (the editor's own validation), for the warnings strip.
    from . import editor  # noqa: PLC0415 -- editor imports nothing of the map
    if civ_doc.get("text"):
        v = editor.validate(cfg, facts, civ_rel, civ_doc["text"])
        warnings += [f"{civ}.json: {m}" for m in v["errors"] + v["warnings"]]
    for h in hero_files:
        hdoc = qa.read(cfg, h["path"])
        if hdoc.get("text"):
            v = editor.validate(cfg, facts, h["path"], hdoc["text"])
            warnings += [f"{h['name']}.json: {m}" for m in v["errors"]]

    return {"civ": civ, "columns": COLS, "nodes": nodes, "edges": edges,
            "warnings": sorted(set(warnings)), "code_errors": facts.errors,
            "civs": [c["civ"] for c in files["civs"]]}


def option_node_id(token: str, graph_ids: set) -> str | None:
    """The map node a logged option token lands on (garrison:3953 -> opt:garrison:<tower>)."""
    if f"opt:{token}" in graph_ids:
        return f"opt:{token}"
    if token.startswith("garrison:") and "opt:garrison:<tower>" in graph_ids:
        return "opt:garrison:<tower>"
    return None
