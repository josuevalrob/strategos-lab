"""Ask any model one logged question again, from the In -> out panel.

Every model goes through the 0 A.D. repo's own model registry
(source/tools/strategos/asker/models: jev, laya, ...), the same adapters the live
asker uses.  One long-lived worker process per model, started on its first ask with
the python solo.sh would use (.claude/strategos/laya-venv when present, laya needs
it), kept warm after.  Keys are the adapters' business (Jev: $UE_KEY or ~/.ue_key);
nothing secret is passed through or returned here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

TOOLS = "source/tools/strategos"
MODELS_DIR = TOOLS + "/asker/models"
VENV_PY = ".claude/strategos/laya-venv/bin/python"

WORKER = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from asker import models
adapter = models.get(sys.argv[2])
t0 = time.perf_counter()
adapter.load()
print(json.dumps({"ready": True, "model": adapter.version,
                  "load_s": round(time.perf_counter() - t0, 1)}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    t0 = time.perf_counter()
    try:
        answers, raws = {}, []
        for qid, q in req["questions"].items():
            a = adapter.ask(models.TypedQuestion(id=qid, state=req["prompt"],
                                                 instructions=q.get("instructions") or "",
                                                 criteria=dict(q.get("criteria") or {})))
            answers[qid] = {"choice": a.choice, "probabilities": a.probabilities}
            raws.append(a.io.get("raw_reply") or "")
        out = {"ok": True, "answers": answers, "raw": "\n".join(raws), "model": adapter.version}
    except Exception as exc:
        out = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    out["ms"] = round((time.perf_counter() - t0) * 1000)
    print(json.dumps(out, default=str), flush=True)
'''


def _questions(request) -> dict | None:
    """The {id: question} map of a logged request: Jev logs {"type": "questions",
    "questions": {...}}, Laya logs {id: question} bare."""
    if not isinstance(request, dict):
        return None
    qs = request.get("questions") or (request.get("response_format") or {}).get("questions")
    if qs:
        return qs
    bare = {k: v for k, v in request.items() if isinstance(v, dict) and "criteria" in v}
    return bare or None


def _first(answers) -> dict:
    if isinstance(answers, dict) and answers:
        a = next(iter(answers.values()))
        if isinstance(a, dict):
            return {"choice": a.get("choice"), "probabilities": a.get("probabilities") or {}}
    return {"choice": None, "probabilities": {}}


class _Worker:
    def __init__(self):
        self.proc = None
        self.info: dict = {}
        self.lock = threading.Lock()


class Asker:
    def __init__(self, repo: Path):
        self.repo = Path(repo)
        self.tools = str(self.repo / TOOLS)
        self.workers: dict[str, _Worker] = {}
        self.workers_lock = threading.Lock()

    def available(self) -> list[str]:
        d = self.repo / MODELS_DIR
        return sorted(p.stem for p in d.glob("*.py") if p.stem not in ("__init__", "base"))

    def _python(self) -> str:
        venv = self.repo / VENV_PY
        return str(venv) if os.access(venv, os.X_OK) else sys.executable

    def _proc(self, model: str, w: _Worker):
        if w.proc is not None and w.proc.poll() is None:
            return w.proc
        env = dict(os.environ, USE_TF="0", HF_HUB_OFFLINE="1")
        w.proc = subprocess.Popen([self._python(), "-c", WORKER, self.tools, model],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=env, cwd=self.tools, bufsize=1)
        first = w.proc.stdout.readline()
        if not first:
            err = (w.proc.stderr.read() or "").strip().splitlines()
            w.proc = None
            raise RuntimeError(f"{model} failed to load: " + (err[-1] if err else "no output"))
        w.info = json.loads(first)
        return w.proc

    def _ask_model(self, model: str, prompt: str, questions: dict) -> dict:
        with self.workers_lock:
            w = self.workers.setdefault(model, _Worker())
        with w.lock:
            try:
                proc = self._proc(model, w)
                proc.stdin.write(json.dumps({"prompt": prompt, "questions": questions}) + "\n")
                proc.stdin.flush()
                out = self._parse(proc.stdout.readline())
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if w.info.get("load_s") is not None:
                out["load_s"] = w.info.pop("load_s")
        return out

    def close(self):
        for w in self.workers.values():
            if w.proc is not None and w.proc.poll() is None:
                w.proc.kill()

    @staticmethod
    def _parse(stdout: str) -> dict:
        try:
            out = json.loads((stdout or "").strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return {"ok": False, "error": (stdout or "no output").strip()[-400:]}
        out.update(_first(out.get("answers")))
        return out

    # -- entry point ------------------------------------------------------------
    def ask(self, model: str, res: dict, prompt: str | None = None, questions: dict | None = None) -> dict:
        """Same prompt + questions to any registered model; ``prompt`` / ``questions``
        override the logged ones."""
        io = res.get("io") or {}
        prompt = prompt or io.get("prompt")
        if not prompt:
            return {"ok": False, "error": "this question has no prompt to send"}
        if model not in self.available():
            return {"ok": False, "error": f"unknown model {model!r}; available: {', '.join(self.available())}"}
        qs = questions or _questions(io.get("request"))
        if not qs:
            return {"ok": False, "error": "no question spec in the request"}
        out = self._ask_model(model, prompt, qs)
        out["asked"] = model
        out.pop("answers", None)
        return out
