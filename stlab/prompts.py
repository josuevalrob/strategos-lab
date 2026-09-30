"""L4: what one question sent to the model and what came back.

New runs (lab L3) log ``io`` per row: the exact prompt, request, raw reply.
Older runs do not; their prompt is REBUILT by running the advisor's own
prompt builder (advisor_jev.build_prompt / question_spec + render_features)
from the Q&A files and code as of the run's header commit (``git archive`` of
that commit into the lab's cache; the 0 A.D. tree is never touched).  If that
commit is not available, the current files are used and the result says so.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

from . import config, gitops, runs

ARCHIVE_PATHS = [config.TOOLS_DIR, config.MOD_DATA]

RUNNER = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
req = json.load(sys.stdin)
import advisor_adapters as A
try:
    import advisor_jev as J
except Exception:
    J = None
from model_client import context_for
if req.get("pin") and hasattr(A, "pin_qa"):
    A.pin_qa(Path(req["pin"]))
out = []
for q in req["questions"]:
    try:
        if req["mode"] == "jev" and J is not None and hasattr(J, "build_prompt"):
            variants = J.parse_variants(req.get("variants") or "") if hasattr(J, "parse_variants") else ()
            text, qid, questions, spec = J.build_prompt(q, variants)
            request = {"model": req.get("model"),
                       "messages": [{"role": "user", "content": text}],
                       "response_format": {"type": "questions", "questions": questions}}
        else:
            spec, qid, questions = A.question_spec(q["kind"], list(q["options"]))
            text = context_for(spec, A.render_features(q))
            request = {"state": text, "questions": questions}
        out.append({"ok": True, "text": text, "qid": qid, "request": request,
                    "generic": bool(spec.get("_generic"))})
    except Exception as exc:
        out.append({"ok": False, "error": type(exc).__name__ + ": " + str(exc)})
json.dump(out, sys.stdout)
'''

_lock = threading.Lock()
_memo: dict = {}


def _mode(adapter: dict) -> str | None:
    name = str((adapter or {}).get("name") or "")
    if name in ("jev", "drex", "claude-api"):
        return "jev"
    if name == "laya":
        return "laya"
    return None


def snapshot_dir(cfg: config.Config, sha: str) -> Path | None:
    """The tools + mod data of ``sha``, extracted once into the lab cache."""
    full = gitops.resolve(cfg.repo, sha)
    if not full:
        return None
    dest = cfg.cache_dir / "snapshots" / full[:12]
    if (dest / ".complete").exists():
        return dest
    with _lock:
        if (dest / ".complete").exists():
            return dest
        data = gitops.archive(cfg.repo, full, ARCHIVE_PATHS)
        tmp = Path(tempfile.mkdtemp(prefix="snap-", dir=_mkdir(cfg.cache_dir / "snapshots")))
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(tmp, filter="data")
        (tmp / ".complete").write_text(full + "\n")
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        tmp.rename(dest)
    return dest


def current_dir(cfg: config.Config) -> Path:
    """A throw-away copy of the working tree's tools + mod data."""
    tmp = Path(tempfile.mkdtemp(prefix="current-", dir=_mkdir(cfg.cache_dir / "snapshots")))
    for rel in ARCHIVE_PATHS:
        src = cfg.path(rel)
        if src.exists():
            shutil.copytree(src, tmp / rel, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return tmp


def overlay_copy(cfg: config.Config, root: Path, snapshot: Path) -> Path:
    """A temp copy of ``root`` with a run's qa_snapshot (questions/, civs/, heroes/) laid over it."""
    tmp = Path(tempfile.mkdtemp(prefix="pinned-", dir=_mkdir(cfg.cache_dir / "snapshots")))
    for rel in ARCHIVE_PATHS:
        if (root / rel).exists():
            shutil.copytree(root / rel, tmp / rel)
    targets = {"questions": config.QUESTIONS_DIR, "civs": config.CIVS_DIR,
               "heroes": config.HEROES_DIR}
    for sub, rel in targets.items():
        src = snapshot / sub
        if src.is_dir():
            shutil.copytree(src, tmp / rel, dirs_exist_ok=True)
    return tmp


def _mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_builder(root: Path, questions: list[dict], mode: str, model: str | None = None,
                variants: str = "", pin: Path | None = None, timeout: float = 90.0) -> list[dict]:
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
           "HOME": str(Path.home()), "LC_ALL": "en_US.UTF-8"}
    req = {"questions": questions, "mode": mode, "model": model, "variants": variants,
           "pin": str(pin) if pin else None}
    proc = subprocess.run([sys.executable, "-c", RUNNER], cwd=str(root / config.TOOLS_DIR),
                          input=json.dumps(req).encode(), capture_output=True, timeout=timeout,
                          env=env)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("prompt builder failed: " + (err[-1] if err else f"exit {proc.returncode}"))
    return json.loads(proc.stdout)


def question_for(run: runs.RunModel, row: dict) -> dict:
    """The q dict the advisor built the prompt from, as far as the log keeps it."""
    header = run.header_of(row)
    civ = runs.civ_of(header, row.get("player"))
    ctx = {"civ": civ} if civ else {}
    return {"kind": row.get("kind"), "options": list(row.get("options") or []),
            "features": row.get("features") or {}, "player": row.get("player"),
            "qid": row.get("qid"), "context": ctx}


def _snapshot_pin(run: runs.RunModel, header: dict) -> Path | None:
    qa_block = header.get("qa") or {}
    snap = qa_block.get("snapshot")
    if not snap:
        return None
    p = run.dir / "advisor" / snap
    return p if p.is_dir() else None


def rebuild(cfg: config.Config, run: runs.RunModel, rows: list[dict],
            force_current: bool = False) -> list[dict]:
    """Rebuilt prompts for rows of one run (same header), labelled with how."""
    if not rows:
        return []
    header = run.header_of(rows[0])
    adapter = header.get("adapter") or {}
    mode = _mode(adapter)
    note_mode = ""
    if mode is None:
        mode = "jev"
        note_mode = (f"the {adapter.get('name') or '?'} adapter reads no prompt; this is what Jev "
                     "would have read")
    qa_block = header.get("qa") or {}
    sha_raw = str(qa_block.get("commit") or header.get("mod_sha") or "")
    dirty = sha_raw.endswith("-dirty") or bool(qa_block.get("dirty"))
    sha = sha_raw.replace("-dirty", "")
    pin = _snapshot_pin(run, header)
    root, label, tmp = None, None, None
    if not force_current and sha:
        try:
            root = snapshot_dir(cfg, sha)
        except (gitops.GitError, OSError, tarfile.TarError, subprocess.SubprocessError):
            root = None
        if root is not None:
            label = "rebuilt"
    if root is None:
        tmp = root = current_dir(cfg)
        label = "rebuilt from current files"
        pin = None
    elif pin is not None:
        # The files the game really read: the run's qa_snapshot over that commit.
        tmp = root = overlay_copy(cfg, root, pin)
    try:
        qs = [question_for(run, r) for r in rows]
        built = run_builder(root, qs, mode, adapter.get("model") or adapter.get("version"),
                            adapter.get("prompt_variant") or "")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    out = []
    for r, b in zip(rows, built):
        logged = (r.get("meta") or {}).get("chars")
        notes = []
        if label == "rebuilt":
            notes.append(f"code + Q&A files as of {sha[:10]}"
                         + (" and the run's qa_snapshot" if pin else ""))
            if dirty:
                notes.append("the tree had uncommitted changes when this game ran: the rebuild "
                             "may differ from what was sent")
        else:
            notes.append("the run's commit is not available: built from the files as they are now")
        if note_mode:
            notes.append(note_mode)
        if b.get("ok"):
            n = len(b["text"])
            if isinstance(logged, int):
                notes.append(f"length check: rebuilt {n} chars, the log says {logged} -> "
                             + ("same length" if n == logged else f"differs by {n - logged}"))
            if r.get("kind") == "play":
                notes.append("heroes in the field / fallen are not in the log; the rebuild "
                             "assumes none unless the length check says otherwise")
        out.append({"mode": label, "sha": sha[:10] if label == "rebuilt" else None,
                    "ok": b.get("ok"), "error": b.get("error"), "prompt": b.get("text"),
                    "request": b.get("request"), "generic": b.get("generic"),
                    "chars_logged": logged, "chars_rebuilt": len(b["text"]) if b.get("ok") else None,
                    "notes": notes})
    return out


def in_out(cfg: config.Config, run: runs.RunModel, idx: int, facts=None) -> dict:
    """Everything the In -> out panel shows for one row."""
    row = run.row(idx)
    header = run.header_of(row)
    entry = run.entry(row, facts.classes if facts else ())
    io_block = row.get("io")
    meta = row.get("meta") or {}
    result = {"entry": entry, "adapter": header.get("adapter"), "mod_sha": header.get("mod_sha"),
              "qa": header.get("qa"), "meta": meta}
    if io_block:
        raw = io_block.get("raw_reply")
        parsed = None
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
        result["io"] = {"mode": "exact", "prompt": io_block.get("prompt"),
                        "request": io_block.get("request"), "raw_reply": raw,
                        "raw_reply_parsed": parsed, "model": io_block.get("model"),
                        "notes": ["exact: logged by the advisor (lab L3)"]}
        return result
    key = (run.id, idx, run.dir.stat().st_mtime_ns if run.dir.exists() else 0)
    if key not in _memo:
        try:
            _memo[key] = rebuild(cfg, run, [row])[0]
        except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
            _memo[key] = {"mode": "failed", "ok": False, "error": str(exc), "notes": []}
    rb = dict(_memo[key])
    rb["raw_reply"] = None
    rb["raw_reply_note"] = ("raw reply not logged before lab L3; the log kept the choice and the "
                            "probabilities: " + json.dumps(meta.get("probabilities"))
                            if meta.get("probabilities") else
                            "raw reply not logged before lab L3 (and no probabilities: "
                            f"outcome {row.get('outcome')})")
    result["io"] = rb
    return result
