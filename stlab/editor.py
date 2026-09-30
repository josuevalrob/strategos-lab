"""L5: validate, apply (write + commit only those paths), history, diff, restore.

Rules (REQUIREMENTS / PLAN L5):
* Apply refuses unless the 0 A.D. repo is on ``strategos/mvp1``; never pushes.
* Only the edited Q&A paths are written and committed (``git commit --only``);
  whatever else is dirty or staged in the repo stays as it was.
* A target file that already had uncommitted changes needs ``confirm_dirty``.
* Validation only removes the impossible (bad JSON, an option the game never
  offers, a hero with no template, a stratagem the head cannot dispatch).  It
  never picks.  An impossible thing the file ALREADY had is a warning, not a
  refusal, so old problems do not block an unrelated edit.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

from . import code, config, gitops, qa

PREFIX_OPTIONS = ("hero:", "tower:", "garrison:")
PETRA_OPTIONS = ("advance", "train:workers", "train:soldiers")


class Refused(Exception):
    def __init__(self, code_: str, message: str, details=None):
        super().__init__(message)
        self.code = code_
        self.message = message
        self.details = details or {}


# -- validation ------------------------------------------------------------------

def _text_lines_ok(value) -> bool:
    if not isinstance(value, list):
        return False
    return all(isinstance(x, str) or (isinstance(x, dict) and isinstance(x.get("text"), str))
               for x in value)


def play_option_universe(cfg: config.Config, facts: code.CodeFacts) -> set:
    """Every exact play option key the game could ever offer (prefix keys included)."""
    out = {"economy", "wall", *PETRA_OPTIONS, *PREFIX_OPTIONS}
    for kind, d in qa.stratagems(cfg).items():
        allowed = facts.stratagem_orders.get(kind, [])
        for order in d.get("orders") or []:
            if order in allowed:
                out.add(order)
    for site in facts.tower_sites:
        out.add(f"tower:{site}")
    return out


def reachable_play_options(cfg: config.Config, facts: code.CodeFacts) -> set:
    """The options some civ with a civ file can really be offered."""
    out = {"economy", "wall", *PETRA_OPTIONS, "hero:", "tower:", "garrison:"}
    strats = qa.stratagems(cfg)
    files = qa.list_files(cfg)
    for c in files["civs"]:
        data = qa.load_json(cfg, c["path"], {}) or {}
        heroes = [{"name": h["name"], "data": qa.load_json(cfg, h["path"], {}) or {}}
                  for h in files["heroes"].get(c["civ"], [])]
        for s in qa.civ_stratagems(data, heroes):
            for order in (strats.get(s["kind"]) or {}).get("orders") or []:
                out.add(order)
    return out


def _check_param_option(cfg, facts, key: str) -> str | None:
    head, sep, param = key.partition(":")
    if not sep:
        return f"option {key!r} is never offered by the game"
    if not param:
        return None if key in PREFIX_OPTIONS else f"option prefix {key!r} is never offered by the game"
    if head == "tower":
        return None if param in facts.tower_sites else f"tower site {param!r} does not exist"
    if head == "hero":
        for civ in [c["civ"] for c in qa.list_files(cfg)["civs"]]:
            if qa.template_exists(cfg, f"units/{civ}/{param}"):
                return None
        return f"no civ has a hero template named {param!r}"
    if head == "garrison":
        return None if param.isdigit() or param == "<tower>" else f"tower id {param!r} is not a number"
    if head == "train":
        return None if key in PETRA_OPTIONS else f"{key!r} is not one of Petra's train orders"
    return f"option {key!r} is never offered by the game"


def validate_question(cfg, facts, rel: str, obj) -> tuple[list, list]:
    errors, warnings = [], []
    if not isinstance(obj, dict):
        return ["a question file must be a JSON object"], []
    questions = obj.get("questions")
    if not isinstance(questions, dict) or not questions:
        return ["'questions' must be an object with at least one question"], []
    universe = play_option_universe(cfg, facts)
    for qid, q in questions.items():
        if not isinstance(q, dict):
            errors.append(f"question {qid!r} must be an object")
            continue
        if q.get("type") != "choice":
            errors.append(f"question {qid!r}: type must be 'choice'")
        if not isinstance(q.get("instructions"), str) or not q["instructions"].strip():
            errors.append(f"question {qid!r}: instructions must be a non-empty string")
        crit = q.get("criteria")
        if not isinstance(crit, dict):
            errors.append(f"question {qid!r}: criteria must be an object option -> text")
            continue
        for k, v in crit.items():
            if not isinstance(v, str) or not v.strip():
                errors.append(f"{qid}: criteria {k!r} must be a non-empty string")
        if qid == "play" or obj.get("criteria_by_prefix"):
            for k in crit:
                if k in universe:
                    continue
                problem = _check_param_option(cfg, facts, k)
                if problem:
                    errors.append(f"{qid}: {problem}")
            reach = reachable_play_options(cfg, facts)
            missing = [o for o in sorted(reach) if o not in crit and
                       not (":" in o and o.split(":")[0] + ":" in crit)]
            if missing:
                warnings.append(f"{qid}: no wording for {', '.join(missing)}: a turn that offers "
                                "one of these gets a generic question")
            continue
        known = code.KNOWN_OPTIONS.get(qid)
        if known is None:
            warnings.append(f"no game code asks a question named {qid!r}")
            continue
        extra = code.PARAM_OPTION_RE.get(qid)
        for k in crit:
            if k not in known and not (extra and extra.match(k)):
                errors.append(f"{qid}: option {k!r} is never offered (the game offers "
                              f"{', '.join(known)})")
        missing = [o for o in known if o not in crit]
        if missing and not obj.get("options_subset"):
            warnings.append(f"{qid}: no wording for {', '.join(missing)}; unless the options asked "
                            "match the criteria exactly, the model gets a generic question")
    return errors, warnings


def validate_civ(cfg, facts, rel: str, obj, pending: set) -> tuple[list, list]:
    errors, warnings = [], []
    if not isinstance(obj, dict):
        return ["a civ file must be a JSON object"], []
    civ = Path(rel).stem
    if obj.get("civ") not in (None, civ):
        warnings.append(f"'civ' says {obj.get('civ')!r} but the file is {civ}.json (the file name wins)")
    if "text" in obj and not _text_lines_ok(obj["text"]):
        errors.append("'text' must be a list of lines (strings or {text, noAction})")
    heroes = obj.get("heroes") or []
    if not isinstance(heroes, list) or not all(isinstance(h, str) for h in heroes):
        errors.append("'heroes' must be a list of hero file names")
        heroes = []
    for h in heroes:
        hp = qa.hero_path(civ, h)
        if not cfg.path(hp).exists() and hp not in pending:
            errors.append(f"hero {h!r}: {hp} does not exist")
    params = obj.get("params") or {}
    if not isinstance(params, dict):
        return errors + ["'params' must be an object"], warnings
    strats = qa.stratagems(cfg)
    for key in ("doctrine", "withoutChoke"):
        kind = params.get(key)
        if kind is None:
            continue
        if kind not in strats:
            errors.append(f"params.{key}: stratagem {kind!r} is not in doctrine.json "
                          f"(known: {', '.join(sorted(strats))})")
            continue
        bad = [o for o in strats[kind].get("orders") or []
               if o not in facts.stratagem_orders.get(kind, [])]
        if bad:
            errors.append(f"params.{key}: {kind} orders {bad} cannot be dispatched (head.js "
                          "STRATAGEM_ORDERS)")
    ho = params.get("heroOrder")
    if isinstance(ho, dict):
        b = ho.get("building")
        if b and not qa.template_exists(cfg, b, civ):
            errors.append(f"params.heroOrder.building {b!r}: no template for {civ}")
        order = ho.get("order") or []
        for t in order:
            if not qa.template_exists(cfg, t, civ):
                errors.append(f"params.heroOrder.order: no template {t!r}")
        urgent = (ho.get("urgent") or {}).get("hero")
        if urgent and urgent not in order:
            warnings.append(f"params.heroOrder.urgent.hero {urgent!r} is not in heroOrder.order")
        # A hero file whose templates are not in the order is never offered as hero:<x>.
        for h in heroes:
            hd = qa.load_json(cfg, qa.hero_path(civ, h), {}) or {}
            ts = hd.get("templates") or []
            if ts and not any(t in order for t in ts):
                warnings.append(f"hero {h!r} ({', '.join(ts)}) is not in params.heroOrder.order: "
                                "the game never offers him as a play option")
    leader = ((params.get("raid") or {}).get("params") or {}).get("leader")
    if leader and not qa.template_exists(cfg, leader, civ):
        warnings.append(f"params.raid.params.leader {leader!r}: no template for {civ}")
    phalanx = ((params.get("params") or {}).get("phalanx"))
    if isinstance(phalanx, str) and not qa.template_exists(cfg, phalanx, civ):
        warnings.append(f"params.params.phalanx {phalanx!r}: no such formation template")
    return errors, warnings


def validate_hero(cfg, facts, rel: str, obj) -> tuple[list, list]:
    errors, warnings = [], []
    if not isinstance(obj, dict):
        return ["a hero file must be a JSON object"], []
    civ = Path(rel).parent.name
    if "text" in obj and not _text_lines_ok(obj["text"]):
        errors.append("'text' must be a list of lines (strings or {text, noAction})")
    ts = obj.get("templates")
    if ts is not None and (not isinstance(ts, list) or not all(isinstance(t, str) for t in ts)):
        errors.append("'templates' must be a list of unit templates")
        ts = []
    for t in ts or []:
        if not qa.template_exists(cfg, t, civ):
            errors.append(f"template {t!r} does not exist")
    params = obj.get("params") or {}
    pb = params.get("playbook") if isinstance(params, dict) else None
    strats = qa.stratagems(cfg)
    if pb is not None and pb not in strats:
        errors.append(f"params.playbook: stratagem {pb!r} is not in doctrine.json")
    civ_doc = qa.load_json(cfg, qa.civ_path(civ), {}) or {}
    if Path(rel).stem not in (civ_doc.get("heroes") or []):
        warnings.append(f"the civ file {civ}.json does not list {Path(rel).stem!r} in 'heroes': "
                        "this file is not in the prompt")
    return errors, warnings


def validate(cfg, facts, rel: str, text: str, pending: set | None = None) -> dict:
    """``{errors, warnings, data}`` for one file's new text."""
    if not config.is_qa_path(rel):
        return {"errors": [f"{rel!r} is not a Q&A file the lab may write"], "warnings": [],
                "data": None}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"errors": [f"invalid JSON: {exc}"], "warnings": [], "data": None}
    kind = config.qa_kind(rel)
    if kind == "question":
        errors, warnings = validate_question(cfg, facts, rel, obj)
    elif kind == "civ":
        errors, warnings = validate_civ(cfg, facts, rel, obj, pending or set())
    else:
        errors, warnings = validate_hero(cfg, facts, rel, obj)
    return {"errors": errors, "warnings": warnings, "data": obj}


def validate_change(cfg, facts, rel: str, text: str, pending: set | None = None) -> dict:
    """Like :func:`validate`, but a problem the current file already has is a warning."""
    res = validate(cfg, facts, rel, text, pending)
    cur = cfg.path(rel)
    if res["errors"] and cur.exists() and res["data"] is not None:
        old = validate(cfg, facts, rel, cur.read_text(encoding="utf-8"), pending)
        before = set(old["errors"])
        still = [e for e in res["errors"] if e in before]
        res["errors"] = [e for e in res["errors"] if e not in before]
        res["warnings"] = [f"(already in the file) {e}" for e in still] + res["warnings"]
    return res


# -- apply / restore -----------------------------------------------------------------

def _new_text(cfg, change: dict) -> str:
    rel = change["path"]
    if isinstance(change.get("text"), str):
        return change["text"]
    if "data" in change:
        cur = cfg.path(rel)
        original = cur.read_text(encoding="utf-8") if cur.exists() else None
        style = None if original else ({"indent": "\t", "ensure_ascii": False, "newline": True}
                                       if config.qa_kind(rel) != "question" else
                                       {"indent": " ", "ensure_ascii": False, "newline": True})
        return qa.dump_data(change["data"], original, style)
    raise Refused("bad_request", f"{rel}: give 'text' or 'data'")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.lab-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def check_branch(cfg: config.Config) -> str:
    br = gitops.branch(cfg.repo)
    if br != cfg.required_branch:
        raise Refused("branch", f"the 0 A.D. repo is on {br!r}, not {cfg.required_branch!r}: "
                                "nothing was written", {"branch": br})
    return br


def apply(cfg: config.Config, facts, changes: list[dict], summary: str,
          confirm_dirty: bool = False, allow_new: bool = False, allow_flags: bool = False,
          live_runs: list | None = None) -> dict:
    """Write the changed files and commit exactly those paths."""
    if not changes:
        raise Refused("bad_request", "nothing to apply")
    check_branch(cfg)
    paths = [c.get("path") for c in changes]
    if len(set(paths)) != len(paths):
        raise Refused("bad_request", "a path is listed twice")
    for rel in paths:
        if not config.is_qa_path(rel):
            raise Refused("path", f"{rel!r} is not a Q&A file the lab may write")
    pending = {p for p in paths if not cfg.path(p).exists()}
    if pending and not allow_new:
        raise Refused("new_file", "creating files is done by the clone action: " + ", ".join(pending))
    texts, warnings, problems = {}, [], {}
    for c in changes:
        rel = c["path"]
        text = _new_text(cfg, c)
        cur = cfg.path(rel)
        if cur.exists() and c.get("base_sha256"):
            now = qa.sha256(cur.read_text(encoding="utf-8"))
            if now != c["base_sha256"]:
                raise Refused("changed_on_disk", f"{rel} changed on disk since it was opened; "
                                                 "reload it and apply again", {"path": rel})
        res = validate_change(cfg, facts, rel, text, pending)
        if allow_flags and res["data"] is not None:
            warnings += [f"{rel}: {e}" for e in res["errors"]]
            res["errors"] = []
        if res["errors"]:
            problems[rel] = res["errors"]
        warnings += [f"{rel}: {w}" for w in res["warnings"]]
        texts[rel] = text
    if problems:
        raise Refused("validation", "validation failed: " + "; ".join(
            f"{p}: {', '.join(e)}" for p, e in problems.items()), {"errors": problems})
    unchanged = [rel for rel in paths if cfg.path(rel).exists() and
                 cfg.path(rel).read_text(encoding="utf-8") == texts[rel]]
    if len(unchanged) == len(paths):
        raise Refused("no_change", "the files already say this: nothing to commit")
    paths = [p for p in paths if p not in unchanged]
    dirty = gitops.status_of(cfg.repo, [p for p in paths if p not in pending])
    if dirty and not confirm_dirty:
        raise Refused("dirty", "these files already had uncommitted changes; applying commits "
                               "them too: " + ", ".join(sorted(dirty)), {"dirty": dirty})
    originals = {rel: (cfg.path(rel).read_text(encoding="utf-8") if cfg.path(rel).exists() else None)
                 for rel in paths}
    message = "Strategos lab: " + (summary.strip() or "edit " + ", ".join(Path(p).name for p in paths))
    try:
        for rel in paths:
            _write_atomic(cfg.path(rel), texts[rel])
        sha = gitops.commit_only(cfg.repo, paths, message.splitlines()[0][:200] +
                                 ("\n\n" + "\n".join(message.splitlines()[1:])
                                  if len(message.splitlines()) > 1 else ""))
    except Exception as exc:
        for rel, old in originals.items():
            if old is None:
                try:
                    cfg.path(rel).unlink()
                except OSError:
                    pass
                try:
                    gitops.git(cfg.repo, "rm", "--cached", "-q", "--ignore-unmatch", "--", rel,
                               read_only=False, check=False)
                except gitops.GitError:
                    pass
            else:
                _write_atomic(cfg.path(rel), old)
        raise Refused("commit_failed", f"commit failed, files put back: {exc}") from exc
    committed = gitops.changed_in(cfg.repo, sha)
    out = {"commit": sha, "short": sha[:10], "message": message.splitlines()[0], "files": committed,
           "warnings": warnings, "unchanged": unchanged, "dirty_before": dirty}
    if live_runs:
        out["live_note"] = live_note(cfg, live_runs, pinned_of(cfg, live_runs))
    return out


def pinned_of(cfg, live_runs: list) -> dict:
    """Run id -> True when its advisor log header carries a qa block (lab L3 pin)."""
    import json as _json  # noqa: PLC0415
    out = {}
    for root in cfg.run_roots():
        for rid in live_runs:
            adv = root.parent / rid / "advisor"
            for f in sorted(adv.glob("*.jsonl")) if adv.is_dir() else []:
                try:
                    with open(f, encoding="utf-8") as fh:
                        out[rid] = bool(_json.loads(fh.readline() or "{}").get("qa"))
                except (OSError, ValueError):
                    pass
    return out


def live_note(cfg, live_runs: list, pinned: dict | None = None) -> str:
    """The note shown while a game runs.  ``pinned``: run id -> its log header has a qa snapshot."""
    pinned = pinned or {}
    if live_runs and all(pinned.get(r) for r in live_runs):
        return ("A game is running (" + ", ".join(live_runs) + "). It reads the Q&A files it "
                "started with (its qa_snapshot), so an edit applied now reaches the NEXT game only.")
    return ("A game is running (" + ", ".join(live_runs) + "). Its log has no qa snapshot (an "
            "advisor from before lab L3 reads the files per question), so its next prompt may "
            "already use an edit applied now, while the game's own options stay as they started.")


def history(cfg: config.Config, rel: str, limit: int = 60) -> list[dict]:
    if not config.is_qa_path(rel):
        raise Refused("path", f"{rel!r} is not a Q&A file")
    return gitops.log(cfg.repo, rel, limit)


def text_at(cfg: config.Config, rel: str, ref: str, draft: str | None = None) -> str | None:
    if ref == "working":
        p = cfg.path(rel)
        return p.read_text(encoding="utf-8") if p.exists() else None
    if ref == "draft":
        return draft
    if not re.match(r"^[0-9a-fA-F]{4,40}$|^HEAD(~\d+)?$", ref):
        raise Refused("bad_request", f"bad version {ref!r}")
    data = gitops.show(cfg.repo, ref, rel)
    return None if data is None else data.decode("utf-8", "replace")


def diff(cfg: config.Config, rel: str, a: str, b: str, draft: str | None = None) -> dict:
    if not config.is_qa_path(rel):
        raise Refused("path", f"{rel!r} is not a Q&A file")
    ta, tb = text_at(cfg, rel, a, draft), text_at(cfg, rel, b, draft)
    lines = list(difflib.unified_diff((ta or "").splitlines(keepends=True),
                                      (tb or "").splitlines(keepends=True),
                                      fromfile=f"{rel} @ {a}", tofile=f"{rel} @ {b}", n=3))
    return {"path": rel, "a": a, "b": b, "diff": "".join(lines), "same": ta == tb,
            "a_exists": ta is not None, "b_exists": tb is not None}


def restore(cfg: config.Config, facts, rel: str, sha: str, confirm_dirty: bool = False,
            live_runs: list | None = None) -> dict:
    """Write the file as it was at ``sha`` and commit that (no checkout)."""
    if not config.is_qa_path(rel):
        raise Refused("path", f"{rel!r} is not a Q&A file")
    if not re.match(r"^[0-9a-fA-F]{4,40}$", sha or ""):
        raise Refused("bad_request", f"bad version {sha!r}")
    data = gitops.show(cfg.repo, sha, rel)
    if data is None:
        raise Refused("not_found", f"{rel} did not exist at {sha[:10]}")
    return apply(cfg, facts, [{"path": rel, "text": data.decode("utf-8")}],
                 f"restore {Path(rel).name} to {sha[:10]}", confirm_dirty=confirm_dirty,
                 allow_flags=True, live_runs=live_runs)
