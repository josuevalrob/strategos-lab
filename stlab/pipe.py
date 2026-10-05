"""The Pipe: how each block's question flows, read from the 0 A.D. files at every change.

One lane per block file (blocks/*.json):
    game facts (mod readers) -> option filters (asker/questions/rules.py + pending)
    -> context steps (the block's "context" list, asker/context/steps.py)
    -> question wording (the block's "questions") -> model (asker/models/*) -> answer
    -> actor (sender -> rlgame clock -> "strategos-request" -> strategos AI -> Petra)
    -> acknowledgements -> back into pending.

Nothing is hand-copied: JSON is read as JSON, Python with ast (registry names, step
factories and their params), JS with small regexes for registry entries.  Every node
carries its file:line and a short snippet.  A missing file or entry shows as an error
on the node, never a crash.

Watch: a thread polls the files' mtimes every second; a change rebuilds the graph and
bumps ``rev``; /api/pipe/events streams each new one (SSE).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from . import config

MOD_SIM = "binaries/data/mods/strategos/simulation"
HELPERS = MOD_SIM + "/helpers"
ASKER = config.TOOLS_DIR + "/asker"
RULES = ASKER + "/questions/rules.py"
QUESTIONS = ASKER + "/questions"
STEPS = ASKER + "/context/steps.py"
CONTEXT_INIT = ASKER + "/context/__init__.py"
MODELS = ASKER + "/models"
TRANSPORTS = ASKER + "/actor/transports"
PENDING = ASKER + "/actor/pending.py"
PIPELINE = ASKER + "/routing/pipeline.py"   # where a leaf is asked and its request sent
CLOCK = config.TOOLS_DIR + "/rlgame/clock.py"
COMMANDS = HELPERS + "/StrategosCommands.js"
BOT = config.AI_DIR + "/_strategosbot.js"
REQUESTS = config.AI_DIR + "/requests.js"

# Directories whose files (and file lists) the watcher follows.
WATCH_DIRS = (config.QUESTIONS_DIR, HELPERS, config.AI_DIR, QUESTIONS, ASKER + "/context",
              MODELS, ASKER + "/actor", TRANSPORTS, ASKER, ASKER + "/routing", config.TOOLS_DIR + "/rlgame")
WATCH_EXT = (".json", ".js", ".py")
SNIPPET = 14
ACK_STATES = ("applied", "started", "finished", "dropped", "rejected")


# -- small readers -----------------------------------------------------------
class Src:
    """One repo file's text, read once per build."""

    def __init__(self, repo: Path, rel: str):
        self.rel = rel
        try:
            self.text = (repo / rel).read_text(encoding="utf-8")
        except OSError:
            self.text = None
        self.lines = self.text.splitlines() if self.text is not None else []

    def ref(self, line: int | None, n: int = SNIPPET, before: int = 0) -> dict:
        if not line:
            return {"path": self.rel, "line": None, "snippet": []}
        lo = max(1, line - before)
        return {"path": self.rel, "line": line,
                "snippet": [[i, self.lines[i - 1]] for i in range(lo, min(len(self.lines), lo + n - 1) + 1)]}

    def find(self, pattern: str, flags=0) -> int | None:
        """1-based line of the first regex match, or None."""
        if self.text is None:
            return None
        m = re.search(pattern, self.text, flags | re.M)
        return self.text.count("\n", 0, m.start()) + 1 if m else None

    def tree(self) -> ast.Module | None:
        if self.text is None:
            return None
        try:
            return ast.parse(self.text)
        except SyntaxError:
            return None


def _missing(rel: str) -> str:
    return f"{rel} is not on disk"


def _funcs(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _doc1(fn) -> str | None:
    d = ast.get_docstring(fn)
    return d.strip().splitlines()[0] if d else None


def _assign(tree: ast.Module, name: str):
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
            return n
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name:
            return n
    return None


def _modules(repo: Path, rel_dir: str, skip=("__init__", "base")) -> list[str]:
    d = repo / rel_dir
    return sorted(p.stem for p in d.glob("*.py") if p.stem not in skip) if d.is_dir() else []


# -- the parts ---------------------------------------------------------------
def readers(repo: Path) -> list[dict]:
    """One node per StrategosReaders.Register("<domain>", ...) in helpers/StrategosReaders_*.js."""
    out = []
    for p in sorted((repo / HELPERS).glob("StrategosReaders_*.js")):
        src = Src(repo, f"{HELPERS}/{p.name}")
        for m in re.finditer(r'StrategosReaders\.Register\(\s*"([^"]+)"', src.text or ""):
            line = src.text.count("\n", 0, m.start()) + 1
            # The doc comment above the Register call, then the call.
            top = line
            while top > 1 and src.lines[top - 2].lstrip().startswith(("*", "/**")):
                top -= 1
            out.append({"id": f"reader:{m.group(1)}", "label": m.group(1), "sub": "reader",
                        **src.ref(line, n=min(SNIPPET + 6, line - top + 10), before=line - top)})
    return out


def rules(repo: Path) -> dict:
    """The filter rules in RULES order (rules.py), plus the 'pending' check (actor/pending.py)."""
    src = Src(repo, RULES)
    tree = src.tree()
    out = {"rules": [], "errors": []}
    if tree is None:
        out["errors"].append(_missing(RULES) if src.text is None else f"{RULES}: syntax error")
    else:
        fns = _funcs(tree)
        node = _assign(tree, "RULES")
        names = [e.id for e in getattr(getattr(node, "value", None), "elts", []) if isinstance(e, ast.Name)]
        if not names:
            out["errors"].append(f"{RULES}: no RULES tuple")
        for name in names:
            fn = fns.get(name)
            out["rules"].append({"id": f"rule:{name}", "label": name,
                                 **src.ref(fn.lineno if fn else None, n=fn.end_lineno - fn.lineno + 1 if fn else 0),
                                 "error": None if fn else f"{name} is not defined in rules.py"})
        out["ref"] = src.ref(node.lineno if node else None, n=6, before=1)
    psrc = Src(repo, PENDING)
    ptree = psrc.tree()
    line = None
    if ptree is not None:
        for c in ptree.body:
            if isinstance(c, ast.ClassDef):
                for f in c.body:
                    if isinstance(f, ast.FunctionDef) and f.name == "blocked":
                        line = f.lineno
    out["pending"] = {"id": "rule:pending", "label": "pending",
                      "sub": "a request the game has not settled", **psrc.ref(line, n=4),
                      "error": None if line else f"no PendingRequests.blocked in {PENDING}"}
    return out


def kinds(repo: Path) -> dict[str, dict]:
    """block kind -> its Question class (questions/<module>.py): file, line, domains, uses rules."""
    out = {}
    for mod in _modules(repo, QUESTIONS, skip=("__init__", "base", "rules")):
        src = Src(repo, f"{QUESTIONS}/{mod}.py")
        tree = src.tree()
        if tree is None:
            continue
        uses_rules = any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "rules"
                         for n in ast.walk(tree))
        for c in tree.body:
            if not isinstance(c, ast.ClassDef):
                continue
            attrs, methods = {}, {}
            for s in c.body:
                if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name):
                    try:
                        attrs[s.targets[0].id] = ast.literal_eval(s.value)
                    except ValueError:
                        pass
                elif isinstance(s, ast.FunctionDef):
                    methods[s.name] = s.lineno
            if isinstance(attrs.get("kind"), str):
                out[attrs["kind"]] = {"module": mod, "class": c.name, "path": src.rel, "line": c.lineno,
                                      "domains": list(attrs.get("domains") or ()), "uses_rules": uses_rules,
                                      "options": src.ref(methods.get("status"), n=8),
                                      "context": src.ref(methods.get("context"), n=8)}
    return out


def steps(repo: Path) -> dict:
    """The context step registry (context/steps.py STEPS): name -> factory, its params, doc."""
    src = Src(repo, STEPS)
    tree = src.tree()
    if tree is None:
        return {"steps": {}, "error": _missing(STEPS) if src.text is None else f"{STEPS}: syntax error"}
    fns = _funcs(tree)
    node = _assign(tree, "STEPS")
    table = {}
    if node is not None and isinstance(node.value, ast.Dict):
        for k, v in zip(node.value.keys, node.value.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str) and isinstance(v, ast.Name):
                fn = fns.get(v.id)
                table[k.value] = {"factory": v.id, "signature": f"({ast.unparse(fn.args)})" if fn else None,
                                  "doc": _doc1(fn) if fn else None,
                                  **src.ref(fn.lineno if fn else None,
                                            n=min(SNIPPET + 6, fn.end_lineno - fn.lineno + 1) if fn else 0)}
    return {"steps": table, "error": None if node is not None else f"{STEPS}: no STEPS registry",
            "registry": src.ref(node.lineno if node else None, n=4)}


def models(repo: Path) -> dict:
    """The model registry (asker/models/<name>.py exporting ADAPTER)."""
    out = []
    for mod in _modules(repo, MODELS):
        src = Src(repo, f"{MODELS}/{mod}.py")
        tree = src.tree()
        line, cls, display = None, None, None
        if tree is not None:
            node = _assign(tree, "ADAPTER")
            cls = node.value.id if node is not None and isinstance(node.value, ast.Name) else None
            for c in tree.body:
                if isinstance(c, ast.ClassDef) and c.name == cls:
                    line = c.lineno
                    for s in c.body:
                        if isinstance(s, ast.Assign) and any(getattr(t, "id", "") == "display" for t in s.targets):
                            try:
                                display = ast.literal_eval(s.value)
                            except ValueError:
                                pass
        out.append({"id": f"model:{mod}", "label": display or mod, "sub": cls, **src.ref(line),
                    "error": None if line else f"no ADAPTER class in {src.rel}"})
    reg = Src(repo, MODELS + "/__init__.py")
    return {"models": out, "registry": reg.ref(reg.find(r"^def get\("), n=5)}


def actor(repo: Path) -> dict:
    """The answer's way into the game and back: one node per hop, from the code."""
    pipe, clock, cmds, bot, req = (Src(repo, r) for r in (PIPELINE, CLOCK, COMMANDS, BOT, REQUESTS))
    transports = _modules(repo, TRANSPORTS)
    cmd = re.search(r'g_Commands\["([^"]+)"\]', cmds.text or "")
    plan = re.search(r"queues\.(\w+)\.addPlan\(new (\w+)", req.text or "")
    handlers = {}
    hb = re.search(r"REQUEST_HANDLERS\s*=\s*\{(.*?)\}", req.text or "", re.S)
    if hb:
        handlers = dict(re.findall(r'"([\w-]+)"\s*:\s*(\w+)', hb.group(1)))

    def node(nid, label, sub, src, line, err):
        return {"id": nid, "label": label, "sub": sub, **src.ref(line), "error": None if line else err}

    nodes = [
        node("act:send", "actor.send", "transports: " + (", ".join(transports) or "none found"),
             pipe, pipe.find(r"transport\.send\("), f"no transport.send( in {PIPELINE}"),
        node("act:clock", "rlgame clock", "Clock.put -> next /step", clock, clock.find(r"^\s+def put\("),
             f"no Clock.put in {CLOCK}"),
        node("act:command", f'"{cmd.group(1)}"' if cmd else "game command", "game command (in the replay)",
             cmds, cmds.find(r"g_Commands\["), f"no g_Commands entry in {COMMANDS}"),
        node("act:ai", "strategos AI", "takeRequest -> handler by block kind", bot,
             bot.find(r"^\s*takeRequest\("), f"no takeRequest in {BOT}"),
        node("act:petra", f"Petra {plan.group(1)}.addPlan" if plan else "Petra queue",
             f"new {plan.group(2)}" if plan else None, req, req.find(r"queues\.\w+\.addPlan\("),
             f"no queues.*.addPlan in {REQUESTS}"),
    ]
    acks = []
    for state in ACK_STATES:
        line = bot.find(r'this\.log\(tag \+ "' + state + " ")
        if line:
            acks.append({"state": state, "line": line})
    first = min((a["line"] for a in acks), default=None)
    nodes.append({"id": "act:acks", "label": "acknowledgements", "sub": " / ".join(a["state"] for a in acks),
                  **bot.ref(first, n=SNIPPET + 34, before=2), "acks": acks,
                  "error": None if acks else f"no ack log lines in {BOT}"})
    return {"nodes": nodes, "handlers": handlers,
            "handlers_ref": req.ref(req.find(r"REQUEST_HANDLERS\s*="), n=4)}


def _json_line(src: Src, key: str, after: int = 1) -> int | None:
    """Line of the first '"key":' at or after line ``after``."""
    for i in range(max(1, after), len(src.lines) + 1):
        if re.search(r'"' + re.escape(key) + r'"\s*:', src.lines[i - 1]):
            return i
    return None


def step_label(spec) -> tuple[str, list]:
    if isinstance(spec, str):
        return spec, []
    if isinstance(spec, list) and spec and isinstance(spec[0], str):
        return spec[0], [str(p) for p in spec[1:]]
    return json.dumps(spec), []


def lanes(repo: Path, kinds_: dict, steps_: dict, handlers: dict) -> list[dict]:
    out = []
    for p in sorted((repo / config.QUESTIONS_DIR).glob("*.json")):
        bid = p.stem
        src = Src(repo, f"{config.QUESTIONS_DIR}/{p.name}")
        lane = {"id": bid, "path": src.rel, "errors": [], "context": None, "lines": [],
                "file": src.ref(1, n=8)}
        try:
            doc = json.loads(src.text or "")
        except json.JSONDecodeError as exc:
            lane["errors"].append(f"{p.name}: not valid JSON ({exc.msg}, line {exc.lineno})")
            out.append(lane)
            continue
        block = doc.get("block") or {}
        kind = block.get("kind")
        k = kinds_.get(kind)
        lane.update({"kind": kind, "building": block.get("building"), "lines": list(block.get("lines") or {}),
                     "resources": block.get("resources") or [], "kind_ref": k,
                     "domains": (k or {}).get("domains") or [], "filters": bool(k and k["uses_rules"]),
                     "handler": handlers.get(kind)})
        if not k:
            lane["errors"].append(f"no question class for kind {kind!r} in {QUESTIONS}/")
        if kind and kind not in handlers:
            lane["errors"].append(f"no request handler for kind {kind!r} in requests.js: answers stay in the log")
        bline = _json_line(src, "block") or 1
        if isinstance(block.get("context"), list):
            cline = _json_line(src, "context", bline)
            lane["context_ref"] = src.ref(cline, n=3)
            lane["context"] = []
            for i, spec in enumerate(block["context"]):
                name, params = step_label(spec)
                st = steps_["steps"].get(name)
                err = None
                if steps_["error"]:
                    err = steps_["error"]
                elif not st:
                    err = f"no step {name!r} in context/steps.py STEPS"
                elif st.get("signature") in ("()",) and params:
                    err = f"{name} takes no params"
                lane["context"].append({"key": json.dumps(spec), "label": name, "params": params,
                                        "spec": spec, "n": i + 1, "error": err,
                                        "signature": (st or {}).get("signature"), "doc": (st or {}).get("doc"),
                                        "path": (st or {}).get("path", STEPS), "line": (st or {}).get("line"),
                                        "snippet": (st or {}).get("snippet", [])})
        q = (doc.get("questions") or {}).get(bid)
        qline = _json_line(src, bid, _json_line(src, "questions") or 1)
        if not isinstance(q, dict):
            lane["errors"].append(f'{p.name}: no "questions"."{bid}" wording')
            q = {}
        crit = q.get("criteria") or {}
        lane["question"] = {"type": q.get("type"), "instructions": q.get("instructions"),
                            "criteria": crit, **src.ref(qline, n=4 + len(crit) + 3)}
        missing = [ln for ln in lane["lines"] if ln not in crit]
        if missing:
            lane["errors"].append(f"no criterion for line(s) {', '.join(missing)}")
        out.append(lane)
    return out


def trunk(lanes_: list[dict]) -> int:
    """How many leading context steps every lane with a context list shares (drawn once)."""
    ctxs = [ln["context"] for ln in lanes_ if ln.get("context")]
    if len(ctxs) < 2:
        return 0
    n = 0
    while all(len(c) > n for c in ctxs) and len({c[n]["key"] for c in ctxs}) == 1:
        n += 1
    return n


def build(cfg: config.Config) -> dict:
    repo = cfg.repo
    k = kinds(repo)
    st = steps(repo)
    act = actor(repo)
    ls = lanes(repo, k, st, act["handlers"])
    r = rules(repo)
    ctx = Src(repo, CONTEXT_INIT)
    errors = list(r["errors"])
    if st["error"]:
        errors.append(st["error"] + ": lanes show their context list without step code")
    return {"lanes": ls, "trunk": trunk(ls), "readers": readers(repo), "rules": r["rules"],
            "rules_ref": r.get("ref"), "pending": r["pending"], "steps": st["steps"],
            "steps_registry": st.get("registry"), "from_spec": ctx.ref(ctx.find(r"^def from_spec\("), n=3),
            "models": models(repo), "actor": act["nodes"], "handlers_ref": act["handlers_ref"],
            "answer": Src(repo, PIPELINE).ref(Src(repo, PIPELINE).find(r"answer = self\.model\.ask\("),
                                              n=SNIPPET, before=2),
            "errors": errors}


# -- overlay: the newest run's last question per block -----------------------
ACK_LINE = re.compile(r"q#(\d+) (" + "|".join(ACK_STATES) + r")\b\s*(.*)$")


def _rules_ns(repo: Path) -> dict | None:
    """rules.py run in a fresh namespace (no import: nothing is written into the 0AD repo)."""
    try:
        text = (repo / RULES).read_text(encoding="utf-8")
        ns = {"__name__": "strategos_rules"}
        exec(compile(text, str(repo / RULES), "exec"), ns)  # noqa: S102 -- our own repo's code
        return ns if "RULES" in ns and "line_status" in ns else None
    except Exception:  # noqa: BLE001 -- the overlay is optional
        return None


def _why_not(ns: dict | None, block: dict | None, line: str, stock: dict) -> dict:
    """Which filter dropped ``line`` at that minute, and why ('pending' when none did)."""
    if ns is None or not isinstance(block, dict):
        return {"rule": None, "reason": "not offered"}
    try:
        for rule in ns["RULES"]:
            nxt, why = ns["line_status"](block, line, stock, rules=(rule,))
            if why:
                return {"rule": rule.__name__, "reason": why}
    except Exception as exc:  # noqa: BLE001
        return {"rule": None, "reason": f"{type(exc).__name__}: {exc}"}
    return {"rule": "pending", "reason": "a request for it is still pending in the game"}


def overlay(cfg: config.Config, registry, lanes_: list[dict]) -> dict | None:
    m = newest_run(registry)
    if m is None:
        return None
    ns = _rules_ns(cfg.repo)
    out = {"run": m.id, "live": m.is_live(cfg.live_window_s), "lanes": {}}
    with m.lock:
        rows = list(m.rows)
    for lane in lanes_:
        bid = lane["id"]
        row = next((r for r in reversed(rows) if r.get("kind") == bid), None)
        o = {}
        if row is not None:
            feats = row.get("features") or {}
            facts = feats.get("facts") or {}
            stock = ((facts.get("economy") or {}).get("stock")) or {}
            block = (facts.get("blocks") or {}).get(bid)
            offered = row.get("options") or []
            dropped = {ln: _why_not(ns, block, ln, stock) for ln in lane["lines"] if ln not in offered}
            eng = m.engine.get(row.get("player"), row.get("qid")) or {}
            acks = []
            texts = ([eng["l3"]["text"]] if eng.get("l3") else []) + [a["text"] for a in eng.get("applied", [])]
            for t in texts:
                a = ACK_LINE.search(t)
                if a and int(a.group(1)) == row.get("qid"):
                    acks.append({"state": a.group(2), "text": t})
            o.update({"minute": feats.get("game_minute"), "qid": row.get("qid"), "player": row.get("player"),
                      "model": row.get("adapter"), "offered": offered, "dropped": dropped,
                      "choice": row.get("choice"), "outcome": row.get("outcome"), "error": row.get("error"),
                      "request": row.get("request"), "acks": acks})
        if row is not None:
            out["lanes"][bid] = o
    return out


def newest_run(registry):
    """The run whose files changed last (registry's 2 s scan cache)."""
    best, best_t = None, -1.0
    for rid, d in registry.scan().items():
        t = _run_mtime(d)
        if t > best_t:
            best, best_t = rid, t
    return registry.get(best) if best else None


def _run_mtime(d: Path) -> float:
    t = 0.0
    for p in [d / "engine.log", d / "asker.log", *d.glob("*.jsonl"), *(d / "advisor").glob("*.jsonl")]:
        try:
            t = max(t, p.stat().st_mtime)
        except OSError:
            pass
    return t


# -- the watcher ---------------------------------------------------------------
def signature(cfg: config.Config, registry=None) -> str:
    h = hashlib.sha1()
    for rel in WATCH_DIRS:
        d = cfg.repo / rel
        try:
            names = sorted(e for e in d.iterdir() if e.suffix in WATCH_EXT)
        except OSError:
            h.update(f"{rel}:absent".encode())
            continue
        for p in names:
            try:
                s = p.stat()
                h.update(f"{p}:{s.st_mtime_ns}:{s.st_size}".encode())
            except OSError:
                pass
    if registry is not None:
        best = max(((_run_mtime(d), rid) for rid, d in registry.scan().items()), default=(0, ""))
        h.update(f"run:{best[1]}:{best[0]}".encode())
    return h.hexdigest()


class Watch:
    """Rebuilds the pipe when a watched file changes; waiters get the new one."""

    def __init__(self, cfg: config.Config, registry, poll_s: float = 1.0):
        self.cfg, self.registry, self.poll_s = cfg, registry, poll_s
        self.cond = threading.Condition()
        self.rev, self.sig, self.data = 0, None, None
        self.thread = None

    def _refresh(self) -> bool:
        sig = signature(self.cfg, self.registry)
        if sig == self.sig:
            return False
        data = build(self.cfg)
        try:
            data["overlay"] = overlay(self.cfg, self.registry, data["lanes"])
        except Exception as exc:  # noqa: BLE001 -- the overlay is optional
            data["overlay"] = {"error": f"{type(exc).__name__}: {exc}", "lanes": {}}
        with self.cond:
            self.sig = sig
            self.rev += 1
            self.data = {"rev": self.rev, "built_at": time.time(), **data}
            self.cond.notify_all()
        return True

    def start(self):
        with self.cond:
            if self.thread is not None:
                return
            self.thread = threading.Thread(target=self._loop, name="pipe-watch", daemon=True)
        self._refresh()
        self.thread.start()

    def _loop(self):
        while True:
            time.sleep(self.poll_s)
            try:
                self._refresh()
            except Exception:  # noqa: BLE001 -- keep watching
                import traceback
                traceback.print_exc()

    def current(self) -> dict:
        self.start()
        with self.cond:
            return self.data

    def wait(self, rev: int, timeout: float = 15.0) -> dict | None:
        """The pipe once its rev is past ``rev``; None after ``timeout`` (keepalive)."""
        self.start()
        with self.cond:
            self.cond.wait_for(lambda: self.rev > rev, timeout)
            return self.data if self.rev > rev else None
