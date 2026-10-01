"""What the game code says: parsed from head.js & co., plus a small hand-kept table.

Parsed (never hand-copied): ORDER_ROUTES, STRATAGEM_ORDERS, MANAGERLESS_ORDERS,
the string constants (ECONOMY, WAIT, ADVANCE, ...), rules.TOWER_SITES,
rules.CLASSES_V2, playState's ``keep`` list.

Hand-kept: the parts of the play question (why each is asked) and how each play
option reaches Petra.  Every entry carries *anchors*: exact source text that the
lab looks up at run time to get the current file:line.  tests/test_code.py
asserts every anchor text still exists (once) so a head.js change fails loudly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

# -- anchors -----------------------------------------------------------------
# id -> (code file key, exact text).  The text must occur exactly once.
ANCHORS: dict[str, tuple[str, str]] = {
    "order_routes": ("head.js", "const ORDER_ROUTES = {"),
    "managerless": ("head.js", "const MANAGERLESS_ORDERS = new Set("),
    "stratagem_orders": ("head.js", "const STRATAGEM_ORDERS = {"),
    "dispatch": ("head.js", "\tdispatch(hint, gameState)\n"),
    "dispatch_allowed": ("head.js", "const allowed = STRATAGEM_ORDERS[hint.stratagem];"),
    "route": ("head.js", "\troute(order)\n"),
    "decide": ("head.js", "\tdecide(gameState, playedTurn, us, them, now)\n"),
    "opponent_ask": ("head.js",
                     'this.desk.ask("opponent_class", rules.CLASSES_V2.slice(), ruleClass,'),
    "play_event": ("head.js", "\tplayEvent(feats, ctx, opClass, pb, order, gameState)\n"),
    "strike_filter": ("head.js", 'const options = (pb.orders || []).filter(o => o !== "strike"'),
    "pass_offer": ("head.js",
                   "const qid = this.offer(kind, options, order || ECONOMY, question, context, "
                   "playedTurn);"),
    "play_kind": ("head.js", 'return def && def.needsChoke ? "pass_order" : "play_order";'),
    "hero_tick": ("head.js", "\theroTick(gameState, playedTurn, block, built, opClass)\n"),
    "hero_open": ("head.js", 'const open = state.phase === "city" && !!built && '
                             "!this.heroesAlive.length && left.length > 0;"),
    "hero_offer": ("head.js", 'const qid = this.offer("hero_next", options, rule, facts,'),
    "decline_tick": ("head.js", "\tdeclineTick(gameState, playedTurn, opp, block, minute)\n"),
    "wall_offer": ("head.js", 'const qid = this.offer("wall_now", ["wall", WAIT], answer, facts,'),
    "tower_ask": ("head.js", "\ttowerAsk(gameState, playedTurn)\n"),
    "tower_offer": ("head.js", 'const qid = this.offer("tower_site", ask.options, ask.rule, ask.facts,'),
    "guard_turn": ("head.js", "\tguardTurn(gameState, playedTurn)\n"),
    "guard_offer": ("head.js", 'const qid = this.offer("garrison_now", ask.options, ask.rule, f,'),
    "offer": ("head.js", "\toffer(kind, options, rule, facts, context, playedTurn)\n"),
    "play_token": ("head.js", "\tplayToken(part, choice)\n"),
    "petra_actions": ("head.js", "\tpetraActions(gameState)\n"),
    "petra_action": ("head.js", "\tpetraAction(gameState, choice, tag)\n"),
    "advance_plan": ("head.js", "queues.majorTech.addPlan(new ResearchPlan(gameState, next, true));"),
    "train_workers_plan": ("head.js", "queues.villager.addPlan(new TrainingPlan(gameState, template,"),
    "train_soldiers_plan": ("head.js",
                            "queues.citizenSoldier.addPlan(new TrainingPlan(gameState, template,"),
    "play_state": ("head.js", "\tplayState(gameState, parts)\n"),
    "play_state_keep": ("head.js", 'const keep = ["game_minute", "phase", "opponent",'),
    "ask_play": ("head.js", "\taskPlay(gameState, playedTurn)\n"),
    "ask_play_desk": ("head.js", 'const qid = this.desk.ask("play", options, rule, facts, context, '
                                 "playedTurn);"),
    "play_rule": ("head.js", "const rule = this.playToken(parts[0], parts[0].rule);"),
    "apply_play_answer": ("head.js", "\tapplyPlayAnswer(settled, tag, gameState)\n"),
    "economy_no_order": ("head.js", 'aiWarn("[strategos] " + tag + " play=economy: no order sent");'),
    "apply_play": ("head.js", "\tapplyPlay(ask, choice, tag, gameState)\n"),
    "apply_play_dedup": ("head.js", 'if (key === this.lastSent && choice !== "strike")'),
    "hero_train_hint": ("head.js", 'const hint = { "stratagem": "hero", "order": "train", '
                                   '"params": params, "q": tag };'),
    "guard_hint": ("head.js", 'this.dispatch({ "stratagem": "guard", "order": "garrison",'),
    "tower_hint": ("head.js", 'this.dispatch({ "stratagem": "towers", "order": "tower",'),
    "wall_hint": ("head.js", 'const hint = { "stratagem": "walls", "order": "wall", '
                             '"params": params, "q": tag };'),
    "hero_js_train": ("hero.js", 'else if (hint.order === "train")'),
    "hero_js_queue": ("hero.js", 'const QUEUE = "strategosHero";'),
    "desk_resolve": ("advisor.js", "\tresolve(now, turnLength)\n"),
    "desk_by_rule": ("advisor.js", 'settled.push(this.byRule(qid, entry, "no answer in "'),
    "tower_sites": ("rules.js", "export const TOWER_SITES = ["),
    "classes_v2": ("rules.js", "export const CLASSES_V2 = ["),
    "doctrine_playbook_orders": ("doctrine.js", '"orders": chosen.orders || [],'),
    "render_play": ("advisor_adapters.py", "def render_play(q: dict) -> str:"),
    "question_spec": ("advisor_adapters.py", "def question_spec(kind: str, options: list[str])"),
    "jev_post": ("advisor_jev.py", "def _post(self, state: str, questions: dict, timeout: float)"),
    "jev_build_prompt": ("advisor_jev.py", "def build_prompt(q: dict, variants"),
    "answer_body": ("advisor.py", "def answer_body(q: dict, result: dict, src: str) -> dict:"),
}

# -- the parts of the play question (hand-kept; head.js 12c-2) ----------------
EVENT_WORDS = {
    "enemy": "enemy soldiers at the pass",
    "clear": "the pass is clear again",
    "playbook": "a new playbook (a hero trained or fallen)",
    "phase": "a new phase",
    "rule": "the rule's own pick changed",
    "slot": "the hero slot opened",
    "possible": "walls became possible",
    "hero-lost": "a hero fell",
    "behind": "our army fell behind theirs",
    "town": "Town reached",
}
PART_EVENT_WORDS = {
    "tower_site": {"enemy": "an enemy soldier near our front gate", "town": "Town reached"},
    "garrison_now": {"enemy": "an enemy soldier within a tower's range"},
}

PARTS: list[dict] = [
    {
        "id": "pass_order", "label": "playbook order (pass)",
        "question_file": "pass_order.json",
        "anchors": ["decide", "play_event", "strike_filter", "pass_offer", "play_kind"],
        "trigger": "Asked on the decision tick when something changed: enemy soldiers at the pass "
                   "(every tick while they are), the pass clear again, a new opponent read, a new "
                   "playbook (a hero trained or fallen), a new phase, or the rule's own pick changed.",
        "options": "the playbook stratagem's orders (doctrine.json stratagems.<kind>.orders) + economy. "
                   "Code drops strike when nothing could reach the choke.",
        "rule": "rules.decideReactive (the civ/hero trigger, force, breakoff, rule blocks)",
        "who": "The civ's playbook (params.doctrine, or a living hero's params.playbook) names the "
               "stratagem; a stratagem that needsChoke asks this part.",
    },
    {
        "id": "play_order", "label": "playbook order (no pass)",
        "question_file": None,
        "anchors": ["decide", "play_event", "pass_offer", "play_kind"],
        "trigger": "Same events as the pass order, for a stratagem that needs no choke "
                   "(params.withoutChoke, e.g. fortify on a map without a pass).",
        "options": "the stratagem's orders + economy",
        "rule": "rules.decideReactive",
        "who": "params.withoutChoke (or a hero's ground.withoutChoke) when the map has no pass.",
    },
    {
        "id": "hero_next", "label": "hero_next",
        "question_file": None,
        "anchors": ["hero_tick", "hero_open", "hero_offer"],
        "trigger": "Asked while the hero slot is open: City phase, the hero building stands, no "
                   "hero in the field, a hero of params.heroOrder.order not fallen. Events: the slot "
                   "opens, a new opponent read while it is open, the rule's pick changes.",
        "options": "heroes of params.heroOrder.order not fallen (hero_x -> hero:hero_x) + wait (-> economy)",
        "rule": "rules.chooseHero (heroOrder.order; heroOrder.urgent when the opponent masses/attacks)",
        "who": "The civ file's params.heroOrder (hidden from the model's prompt since 12d-2).",
    },
    {
        "id": "tower_site", "label": "tower_site",
        "question_file": "tower_site.json",
        "anchors": ["tower_ask", "tower_offer"],
        "trigger": "Town reached, or an enemy soldier within 250 m of the front gate (each at most "
                   "once per 3 game minutes); asked only with two or more options.",
        "options": "sites where a tower can stand now (choke/fields/gate -> tower:<site>) + wait (-> economy)",
        "rule": "rules.towerSiteRule: choke if nothing holds the pass, else gate, else wait",
        "who": "The civ file's params.towerSite block.",
    },
    {
        "id": "garrison_now", "label": "garrison_now",
        "question_file": "garrison_now.json",
        "anchors": ["guard_turn", "guard_offer"],
        "trigger": "An enemy soldier within a tower's own range (60 m) of one of our towers with free "
                   "slots (once per tower per 2 game minutes).",
        "options": "garrison (-> garrison:<tower id>) + keep_working (-> economy); garrison only when possible",
        "rule": "rules.garrisonNowRule (the fortify rule)",
        "who": "The civ file's params.garrisonNow block.",
    },
    {
        "id": "wall_now", "label": "wall_now (Decline)",
        "question_file": "wall_now.json",
        "anchors": ["decline_tick", "wall_offer"],
        "trigger": "Walls possible (phase + street grid), a hero fell, or our army fell behind "
                   "theirs; the rule's pick changed.",
        "options": "wall + wait (-> economy)",
        "rule": "rules.declineRule: wall when the heroes are gone and our army stayed behind",
        "who": "The civ file's params.decline block.",
    },
    {
        "id": "petra", "label": "Petra's own orders",
        "question_file": None,
        "anchors": ["petra_actions", "ask_play"],
        "trigger": "Added to every play question when Petra could carry them out now "
                   "(researchManager / trainMoreWorkers' own tests).",
        "options": "advance (next phase), train:workers, train:soldiers",
        "rule": "none: never a part's default",
        "who": "Petra's own checks; no Q&A file.",
    },
]

# The parts' options as play tokens (head.js playToken).
PART_TOKENS = {
    "hero_next": {"wait": "economy", "*": "hero:{choice}"},
    "wall_now": {"wait": "economy", "*": "{choice}"},
    "tower_site": {"wait": "economy", "*": "tower:{choice}"},
    "garrison_now": {"keep_working": "economy", "*": "garrison:{tower}"},
}

# Option -> Petra, for everything that is not a playbook order (hand-kept).
DIRECT_ACTIONS = {
    "economy": {"stratagem": None, "order": None, "label": "no order: Petra's own economy continues",
                "queues": [], "anchors": ["apply_play_answer", "economy_no_order"]},
    "advance": {"stratagem": None, "order": None,
                "label": "ResearchPlan: the next phase (majorTech queue)",
                "queues": ["majorTech queue"], "anchors": ["petra_action", "advance_plan"]},
    "train:workers": {"stratagem": None, "order": None,
                      "label": "TrainingPlan: a batch of workers (villager queue)",
                      "queues": ["villager queue"], "anchors": ["petra_action", "train_workers_plan"]},
    "train:soldiers": {"stratagem": None, "order": None,
                       "label": "TrainingPlan: a batch of citizen soldiers (citizenSoldier queue)",
                       "queues": ["citizenSoldier queue"],
                       "anchors": ["petra_action", "train_soldiers_plan"]},
    "hero:": {"stratagem": "hero", "order": "train",
              "label": "hero stratagem trains him at the hero building (strategosHero queue)",
              "queues": ["strategosHero queue"],
              "anchors": ["hero_train_hint", "hero_js_train", "hero_js_queue"]},
    "tower:": {"stratagem": "towers", "order": "tower",
               "label": "a defense tower at the site (ConstructionPlan)",
               "queues": [], "anchors": ["tower_hint"]},
    "garrison:": {"stratagem": "guard", "order": "garrison",
                  "label": "the nearest workers go into the tower",
                  "queues": [], "anchors": ["guard_hint"]},
    "wall": {"stratagem": "walls", "order": "wall",
             "label": "Decline: palisade walls + bolt shooters",
             "queues": [], "anchors": ["wall_hint"]},
}

# Question ids of the other question files, and the options the game can offer.
KNOWN_OPTIONS = {
    "pass_order": ["hold", "strike", "fallback", "economy"],
    "ambush_order": ["hold", "strike", "fallback"],
    "garrison_now": ["garrison", "keep_working"],
    "tower_site": ["choke", "fields", "gate", "wait"],
    "wall_now": ["wall", "wait"],
    "raid_target": ["fields", "mines", "storehouses", "market", "wait"],
    "raid_return": ["return", "stay"],
    "camp_move": ["stay", "move"],
    "rams_tower": ["wait"],          # + t<id> per question
    "hero_next": [],                 # hero_x + wait, per civ
    "opponent_class": ["booming", "massing", "attacking", "defending", "dying"],
}
PARAM_OPTION_RE = {"rams_tower": re.compile(r"^t\d+$")}


@dataclass
class CodeFacts:
    order_routes: dict = field(default_factory=dict)       # order -> [managers]
    order_lines: dict = field(default_factory=dict)        # order -> line in head.js
    stratagem_orders: dict = field(default_factory=dict)   # stratagem -> [orders]
    stratagem_lines: dict = field(default_factory=dict)
    managerless: list = field(default_factory=list)
    consts: dict = field(default_factory=dict)
    tower_sites: list = field(default_factory=list)
    classes: list = field(default_factory=list)
    play_state_keys: list = field(default_factory=list)
    anchors: dict = field(default_factory=dict)            # id -> {file, path, line, text, count}
    errors: list = field(default_factory=list)

    def anchor(self, aid: str) -> dict:
        return self.anchors.get(aid) or {"id": aid, "line": None, "found": False}


def _strip_line_comment(line: str) -> str:
    out, in_str, quote = [], False, ""
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if c == quote:
                in_str = False
        else:
            if c in "\"'":
                in_str, quote = True, c
            elif c == "/" and line[i:i + 2] == "//":
                break
            out.append(c)
        i += 1
    return "".join(out)


def parse_object_of_lists(src: str, name: str) -> tuple[dict, dict]:
    """``const NAME = { "k": ["a", "b"], ... };`` -> ({k: [a, b]}, {k: line})."""
    m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*\{", src)
    if not m:
        raise ValueError(f"{name} not found")
    start_line = src.count("\n", 0, m.start()) + 1
    body_lines = src[m.end():].split("\n")
    result, lines = {}, {}
    depth = 1
    for offset, raw in enumerate(body_lines):
        line = _strip_line_comment(raw)
        # Block comments inside the literal (/* ... */) are rare; drop them.
        line = re.sub(r"/\*.*?\*/", "", line)
        for km in re.finditer(r'"([^"]+)"\s*:\s*\[([^\]]*)\]', line):
            key = km.group(1)
            vals = re.findall(r'"([^"]*)"', km.group(2))
            result[key] = vals
            lines[key] = start_line + offset
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            break
    if not result:
        raise ValueError(f"{name} parsed empty")
    return result, lines


def parse_string_set(src: str, name: str) -> list[str]:
    m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*new\s+Set\(\[([^\]]*)\]\)", src)
    return re.findall(r'"([^"]*)"', m.group(1)) if m else []


def parse_string_consts(src: str) -> dict:
    return {m.group(1): m.group(2)
            for m in re.finditer(r'^const\s+([A-Z_][A-Z0-9_]*)\s*=\s*"([^"]*)";', src, re.M)}


def parse_export_list(src: str, name: str) -> list[str]:
    m = re.search(r"export\s+const\s+" + re.escape(name) + r"\s*=\s*\[([^\]]*)\]", src)
    return re.findall(r'"([^"]*)"', m.group(1)) if m else []


def parse_keep_list(src: str) -> list[str]:
    m = re.search(r'const keep = \[("game_minute".*?)\];', src, re.S)
    return re.findall(r'"([^"]*)"', m.group(1)) if m else []


def find_anchor(text: str, needle: str) -> tuple[int | None, int]:
    """(1-based line of the first occurrence, number of occurrences)."""
    count = text.count(needle)
    if not count:
        return None, 0
    idx = text.index(needle)
    # An anchor that starts with "\t..." or a newline points at its first non-blank char.
    lead = len(needle) - len(needle.lstrip("\n"))
    return text.count("\n", 0, idx + lead) + 1, count


def read_code(cfg: config.Config) -> dict[str, str]:
    out = {}
    for key, rel in config.CODE_FILES.items():
        p = cfg.path(rel)
        out[key] = p.read_text(encoding="utf-8") if p.exists() else ""
    return out


_cache: dict = {}


def load(cfg: config.Config) -> CodeFacts:
    """Parse the code files; cached on their mtimes."""
    stamp = []
    for rel in config.CODE_FILES.values():
        p = cfg.path(rel)
        stamp.append(p.stat().st_mtime_ns if p.exists() else 0)
    key = (str(cfg.repo), tuple(stamp))
    if key in _cache:
        return _cache[key]
    files = read_code(cfg)
    facts = CodeFacts()
    # A code file that is not in the repo (strategos/v2 has none of mvp1's map code) leaves its
    # part of the map empty; only a file that exists and no longer matches is an error.
    present = {k for k, rel in config.CODE_FILES.items() if cfg.path(rel).exists()}
    head = files.get("head.js", "")
    try:
        facts.order_routes, facts.order_lines = parse_object_of_lists(head, "ORDER_ROUTES")
    except ValueError as exc:
        if "head.js" in present:
            facts.errors.append(f"head.js: {exc}")
    try:
        facts.stratagem_orders, facts.stratagem_lines = parse_object_of_lists(head, "STRATAGEM_ORDERS")
    except ValueError as exc:
        if "head.js" in present:
            facts.errors.append(f"head.js: {exc}")
    facts.managerless = parse_string_set(head, "MANAGERLESS_ORDERS")
    facts.consts = parse_string_consts(head)
    rules = files.get("rules.js", "")
    facts.tower_sites = parse_export_list(rules, "TOWER_SITES") or ["choke", "fields", "gate"]
    facts.classes = parse_export_list(rules, "CLASSES_V2") or KNOWN_OPTIONS["opponent_class"]
    facts.play_state_keys = parse_keep_list(head)
    for aid, (fkey, needle) in ANCHORS.items():
        line, count = find_anchor(files.get(fkey, ""), needle)
        facts.anchors[aid] = {"id": aid, "file": fkey, "path": config.CODE_FILES[fkey],
                              "line": line, "count": count, "found": line is not None,
                              "text": needle.strip("\n")}
        if line is None and fkey in present:
            facts.errors.append(f"anchor {aid!r} not found in {fkey}: {needle.strip()!r}")
    _cache.clear()
    _cache[key] = facts
    return facts


def snippet(cfg: config.Config, fkey: str, line: int, before: int = 6, after: int = 14) -> dict:
    rel = config.CODE_FILES.get(fkey)
    if not rel:
        raise KeyError(fkey)
    lines = cfg.path(rel).read_text(encoding="utf-8").split("\n")
    lo = max(1, int(line) - before)
    hi = min(len(lines), int(line) + after)
    return {"file": fkey, "path": rel, "line": int(line), "from": lo,
            "lines": [{"n": n, "text": lines[n - 1]} for n in range(lo, hi + 1)]}


def play_token(part_kind: str, choice: str, tower: str | None = None) -> str:
    """head.js playToken for a part's own option."""
    table = PART_TOKENS.get(part_kind)
    if not table:
        return choice
    pattern = table.get(choice) or table["*"]
    return pattern.format(choice=choice, tower=tower if tower is not None else "<tower>")
