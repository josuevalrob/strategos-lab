"""The lab's HTTP server: one page + a JSON API.  127.0.0.1 only."""

from __future__ import annotations

import json
import mimetypes
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import ask, clone, code, config, editor, gitops, map3d, mapgraph, models, prompts, qa, runs

STATIC = config.LAB_ROOT / "static"


class Lab:
    """Shared state behind the handler."""

    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.registry = runs.Registry(cfg)
        self.write_lock = threading.Lock()
        self.asker = ask.Asker(cfg.repo)

    def facts(self) -> code.CodeFacts:
        return code.load(self.cfg)

    # -- GET ----------------------------------------------------------------
    def info(self, q):
        cfg = self.cfg
        files = qa.list_files(cfg)
        paths = [c["path"] for c in files["civs"]] + [q_["path"] for q_ in files["questions"]] + \
            [h["path"] for hs in files["heroes"].values() for h in hs]
        live = self.registry.live_runs()
        return {"repo": str(cfg.repo), "branch": gitops.branch(cfg.repo),
                "required_branch": cfg.required_branch, "head": gitops.head(cfg.repo, short=True),
                "dirty_qa": gitops.status_of(cfg.repo, paths), "live_runs": live,
                "live_note": editor.live_note(cfg, live, editor.pinned_of(cfg, live)) if live else None,
                "code_errors": self.facts().errors}

    def files(self, q):
        cfg = self.cfg
        out = qa.list_files(cfg)
        all_paths = [c["path"] for c in out["civs"]] + [x["path"] for x in out["questions"]] + \
            [h["path"] for hs in out["heroes"].values() for h in hs]
        out["status"] = gitops.status_of(cfg.repo, all_paths)
        return out

    def file(self, q):
        rel = _one(q, "path")
        if not config.is_qa_path(rel):
            raise editor.Refused("path", f"{rel!r} is not a Q&A file")
        doc = qa.read(self.cfg, rel)
        doc["status"] = gitops.status_of(self.cfg.repo, [rel]).get(rel)
        doc["kind"] = config.qa_kind(rel)
        if doc["exists"]:
            doc["validation"] = editor.validate(self.cfg, self.facts(), rel, doc["text"])
            doc["validation"].pop("data", None)
        doc["choices"] = self.choices(rel)
        return doc

    def choices(self, rel: str) -> dict:
        """What the form offers for this file: stratagems, hero templates, known options."""
        facts = self.facts()
        strats = {k: (v.get("orders") or []) for k, v in qa.stratagems(self.cfg).items()}
        out = {"stratagems": strats, "classes": facts.classes}
        kind = config.qa_kind(rel)
        if kind in ("civ", "hero"):
            civ = Path(rel).stem if kind == "civ" else Path(rel).parent.name
            out["civ"] = civ
            out["hero_templates"] = qa.hero_templates_of(self.cfg, civ)
            out["hero_files"] = [h["name"] for h in qa.list_files(self.cfg)["heroes"].get(civ, [])]
        if kind == "doctrine":
            play = qa.load_json(self.cfg, qa.question_path("play"), {}) or {}
            out["stratagem_orders"] = facts.stratagem_orders
            out["order_routes"] = facts.order_routes
            out["play_criteria"] = ((play.get("questions") or {}).get("play") or {}).get("criteria") or {}
            out["used_by"] = editor.stratagem_users(self.cfg)
            out["note"] = editor.DOCTRINE_NOTE
        if kind == "question":
            known = {k: list(v) for k, v in code.KNOWN_OPTIONS.items()}
            known["play"] = sorted(editor.play_option_universe(self.cfg, facts))
            out["known_options"] = known
        return out

    def map(self, q):
        return mapgraph.build(self.cfg, _one(q, "civ", "spart"))

    def map3d(self, q):
        return map3d.build(self.cfg, _one(q, "civ", "spart"))

    def source(self, q):
        fkey = _one(q, "file")
        if fkey not in config.CODE_FILES:
            raise editor.Refused("path", f"unknown code file {fkey!r}")
        return code.snippet(self.cfg, fkey, int(_one(q, "line", "1")),
                            int(_one(q, "before", "6")), int(_one(q, "after", "16")))

    def runs(self, q):
        return {"runs": [m.info(self.cfg.live_window_s) for m in self.registry.all()]}

    def _run(self, q):
        rid = _one(q, "run")
        if rid == "newest":
            m = self.registry.newest()
            if m is None:
                raise editor.Refused("not_found", "no runs found")
            return m
        try:
            return self.registry.get(rid)
        except KeyError as exc:
            raise editor.Refused("not_found", str(exc)) from exc

    def timeline(self, q):
        m = self._run(q)
        kind = _one(q, "kind", "")
        since = int(_one(q, "since", "0") or 0)
        facts = self.facts()
        return {"run": m.info(self.cfg.live_window_s), "rev": m.rev,
                "entries": m.timeline(kind or None, since, facts.classes)}

    def live(self, q):
        return self.timeline(q)

    def question(self, q):
        m = self._run(q)
        idx = int(_one(q, "idx"))
        if not 0 <= idx < len(m.rows):
            raise editor.Refused("not_found", f"no row {idx} in {m.id}")
        facts = self.facts()
        res = prompts.in_out(self.cfg, m, idx, facts)
        graph = mapgraph.build(self.cfg, (res["entry"] and _civ_of(m, idx)) or "spart")
        ids = {n["id"] for n in graph["nodes"]}
        res["map_path"] = map_path(res["entry"], ids, graph["edges"])
        return res

    def models(self, q):
        kinds = [k for k in _one(q, "kinds", "").split(",") if k]
        return models.group(self.registry, kinds or None)

    def history(self, q):
        rel = _one(q, "path")
        return {"path": rel, "log": editor.history(self.cfg, rel)}

    def diff(self, q):
        return editor.diff(self.cfg, _one(q, "path"), _one(q, "a", "HEAD"), _one(q, "b", "working"))

    def show(self, q):
        rel = _one(q, "path")
        if not config.is_qa_path(rel):
            raise editor.Refused("path", f"{rel!r} is not a Q&A file")
        text = editor.text_at(self.cfg, rel, _one(q, "sha"))
        return {"path": rel, "sha": _one(q, "sha"), "text": text, "exists": text is not None}

    def civcodes(self, q):
        return {"codes": qa.civ_codes(self.cfg),
                "civ_files": [c["civ"] for c in qa.list_files(self.cfg)["civs"]]}

    # -- POST ---------------------------------------------------------------
    def p_ask(self, body):
        res = self.question({"run": [str(body.get("run") or "")], "idx": [str(body.get("idx"))]})
        return ask_ok(self.asker.ask(str(body.get("model")), res))

    def p_validate(self, body):
        rel = body.get("path")
        if not config.is_qa_path(rel):
            raise editor.Refused("path", f"{rel!r} is not a Q&A file")
        text = body.get("text")
        if not isinstance(text, str):
            text = qa.dump_data(body.get("data"), qa.read(self.cfg, rel).get("text"))
        res = editor.validate_change(self.cfg, self.facts(), rel, text)
        res.pop("data", None)
        return res

    def p_diff(self, body):
        if not config.is_qa_path(body.get("path")):
            raise editor.Refused("path", f"{body.get('path')!r} is not a Q&A file")
        draft = body.get("draft")
        if not isinstance(draft, str) and "draft_data" in body:
            draft = qa.dump_data(body["draft_data"], qa.read(self.cfg, body.get("path")).get("text"))
        return editor.diff(self.cfg, body.get("path"), body.get("a", "working"),
                           body.get("b", "draft"), draft)

    def p_apply(self, body):
        with self.write_lock:
            return editor.apply(self.cfg, self.facts(), body.get("changes") or [],
                                str(body.get("summary") or ""), bool(body.get("confirm_dirty")),
                                live_runs=self.registry.live_runs())

    def p_restore(self, body):
        with self.write_lock:
            return editor.restore(self.cfg, self.facts(), body.get("path"), str(body.get("sha") or ""),
                                  bool(body.get("confirm_dirty")),
                                  live_runs=self.registry.live_runs())

    def p_format(self, body):
        """Form data -> the exact text Apply would write (for the raw view)."""
        rel = body.get("path")
        if not config.is_qa_path(rel):
            raise editor.Refused("path", f"{rel!r} is not a Q&A file")
        return {"text": qa.dump_data(body.get("data"), qa.read(self.cfg, rel).get("text"))}

    def p_clone_preview(self, body):
        return clone.preview(self.cfg, self.facts(), body.get("src", "spart"), body.get("dst", ""),
                             body.get("name"))

    def p_clone(self, body):
        with self.write_lock:
            return clone.clone(self.cfg, self.facts(), body.get("src", "spart"), body.get("dst", ""),
                               body.get("name"), live_runs=self.registry.live_runs())


def ask_ok(out: dict) -> dict:
    """The asker's own ok=False is an answer to show, not an HTTP error."""
    return {"ok": True, "result": out}


def _one(q: dict, key: str, default: str | None = None) -> str:
    v = q.get(key)
    if not v:
        if default is None:
            raise editor.Refused("bad_request", f"missing parameter {key!r}")
        return default
    return v[0]


def _civ_of(m: runs.RunModel, idx: int) -> str | None:
    row = m.row(idx)
    return runs.civ_of(m.header_of(row), row.get("player"))


def map_path(entry: dict, ids: set, edges: list | None = None) -> dict:
    """The map nodes a question lit up: parts asked, options offered, the chosen path."""
    kind = entry.get("kind")
    game_choice = (entry.get("game") or {}).get("choice") or entry.get("choice")
    parts, offered, chosen, rule = [], [], None, None

    def tok(o):
        if kind in code.PART_TOKENS:
            return code.play_token(kind, o, "<tower>")
        return o

    if kind == "play":
        for p in entry.get("trigger") or []:
            if f"part:{p['part']}" in ids:
                parts.append(f"part:{p['part']}")
        for o in entry.get("options") or []:
            if o in ("advance", "train:workers", "train:soldiers") and "part:petra" not in parts:
                parts.append("part:petra")
    elif f"part:{kind}" in ids:
        parts.append(f"part:{kind}")
    elif kind == "opponent_class":
        parts.append("in:opponent")
    for o in entry.get("options") or []:
        nid = mapgraph.option_node_id(tok(o), ids)
        if nid:
            offered.append(nid)
    if game_choice:
        chosen = mapgraph.option_node_id(tok(game_choice), ids)
    if entry.get("rule"):
        rule = mapgraph.option_node_id(tok(entry["rule"]), ids)
    applied = (entry.get("game") or {}).get("applied") or {}
    act, mgrs = None, []
    order = applied.get("order")
    if order and f"act:{order}" in ids:
        act = f"act:{order}"
    elif order and chosen:
        # An L3 line names the order only ("hold"): the action the chosen option leads to.
        for e in edges or []:
            if e["source"] == chosen and e["target"].endswith("/" + order):
                act = e["target"]
                break
    elif chosen in ("opt:advance", "opt:train:workers", "opt:train:soldiers"):
        act = "act:" + chosen[4:]
    elif chosen == "opt:economy":
        act = "act:economy"
    for m_ in applied.get("managers") or []:
        if f"mgr:{m_}" in ids:
            mgrs.append(f"mgr:{m_}")
    return {"parts": parts, "offered": offered, "chosen": chosen, "rule": rule, "action": act,
            "managers": mgrs, "on_map": kind == "play" or bool(parts)}


GET_ROUTES = {"/api/info": "info", "/api/files": "files", "/api/file": "file", "/api/map": "map",
              "/api/map3d": "map3d",
              "/api/source": "source", "/api/runs": "runs", "/api/timeline": "timeline",
              "/api/live": "live", "/api/question": "question", "/api/models": "models",
              "/api/history": "history", "/api/diff": "diff", "/api/show": "show",
              "/api/civcodes": "civcodes"}
POST_ROUTES = {"/api/ask": "p_ask", "/api/validate": "p_validate", "/api/diff": "p_diff", "/api/apply": "p_apply",
               "/api/format": "p_format",
               "/api/restore": "p_restore", "/api/clone/preview": "p_clone_preview",
               "/api/clone": "p_clone"}
STATUS = {"bad_request": 400, "path": 400, "not_found": 404, "validation": 422, "branch": 409,
          "dirty": 409, "changed_on_disk": 409, "no_change": 409, "new_file": 400, "clone": 400,
          "commit_failed": 500}


class Handler(BaseHTTPRequestHandler):
    lab: Lab = None  # set by make_server
    quiet = True
    server_version = "StrategosLab/1.0"

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "")

    def _call(self, fn, arg):
        try:
            res = fn(arg)
            if isinstance(res, dict) and "ok" not in res:
                res = {"ok": True, **res}
            self._json(200, res)
        except editor.Refused as exc:
            self._json(STATUS.get(exc.code, 400), {"ok": False, "code": exc.code, "error": exc.message,
                                                   "details": exc.details})
        except (gitops.GitError, ValueError, KeyError) as exc:
            self._json(400, {"ok": False, "code": "error", "error": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:  # noqa: BLE001 -- a server must answer
            traceback.print_exc()
            self._json(500, {"ok": False, "code": "internal", "error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "bad Host"})
        url = urlparse(self.path)
        if url.path in GET_ROUTES:
            return self._call(getattr(self.lab, GET_ROUTES[url.path]), parse_qs(url.query))
        if url.path in ("/", "/index.html"):
            return self._static("index.html")
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "bad Host"})
        url = urlparse(self.path)
        if url.path not in POST_ROUTES:
            return self._json(404, {"ok": False, "error": "not found"})
        # A custom header + JSON body: a page on another origin cannot send this without CORS.
        if self.headers.get("X-Lab") != "1" or "json" not in (self.headers.get("Content-Type") or ""):
            return self._json(403, {"ok": False, "error": "missing X-Lab header / JSON body"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5_000_000:
            return self._json(413, {"ok": False, "error": "body too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"ok": False, "error": "body is not JSON"})
        if not isinstance(body, dict):
            return self._json(400, {"ok": False, "error": "body must be an object"})
        self._call(getattr(self.lab, POST_ROUTES[url.path]), body)

    def _static(self, rel: str):
        path = (STATIC / rel).resolve()
        if STATIC.resolve() not in path.parents and path != STATIC.resolve() or not path.is_file():
            return self._json(404, {"ok": False, "error": "not found"})
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "text/javascript"):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)


def make_server(cfg: config.Config, port: int = 8765, host: str = "127.0.0.1",
                quiet: bool = True) -> ThreadingHTTPServer:
    lab = Lab(cfg)
    handler = type("LabHandler", (Handler,), {"lab": lab, "quiet": quiet})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    srv.lab = lab
    return srv
