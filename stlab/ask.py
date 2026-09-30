"""Ask Jev or Laya one logged question again, from the In -> out panel.

Both go through the 0 A.D. repo's own advisor code (source/tools/strategos), in a
subprocess: Jev = one short python3 process per ask (advisor_jev.JevAdapter._post_io);
Laya = one long-lived process in the Laya venv (model load ~40 s, kept warm after).
The Jev key is read from UE_KEY or ~/.ue_key into the child's env only; never returned.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

TOOLS = "source/tools/strategos"
LAYA_PY = ".claude/strategos/laya-venv/bin/python"

JEV_SCRIPT = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
import advisor_jev as J
req = json.load(sys.stdin)
t0 = time.perf_counter()
try:
    answers, io = J.JevAdapter(allow_network=True, timeout_cap=60.0)._post_io(req["prompt"], req["questions"], 60.0)
    out = {"ok": True, "answers": answers, "raw": io["raw_reply"], "model": io["model"]}
except Exception as exc:
    out = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
out["ms"] = round((time.perf_counter() - t0) * 1000)
print(json.dumps(out, default=str))
'''

LAYA_SCRIPT = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from model_client import ModelClient
import advisor_adapters as A
c = ModelClient(timeout=120.0)
c.load()
print(json.dumps({"ready": True, "model": f"{c.repo}/{c.subfolder}", "load_s": round(c.load_seconds, 1)}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    t0 = time.perf_counter()
    try:
        questions = req.get("questions") or A.question_spec(req["kind"], list(req["options"]))[2]
        res = c.agent.predict(req["prompt"], questions)
        out = {"ok": True, "answers": c._validate(res, questions), "raw": json.dumps(res, default=str),
               "model": f"{c.repo}/{c.subfolder}"}
    except Exception as exc:
        out = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    out["ms"] = round((time.perf_counter() - t0) * 1000)
    print(json.dumps(out, default=str), flush=True)
'''


def _questions(request) -> dict | None:
    if not isinstance(request, dict):
        return None
    return request.get("questions") or (request.get("response_format") or {}).get("questions")


def _first(answers) -> dict:
    if isinstance(answers, dict) and answers:
        a = next(iter(answers.values()))
        if isinstance(a, dict):
            return {"choice": a.get("choice"), "probabilities": a.get("probabilities") or {}}
    return {"choice": None, "probabilities": {}}


class Asker:
    def __init__(self, repo: Path):
        self.repo = Path(repo)
        self.tools = str(self.repo / TOOLS)
        self.laya = None
        self.laya_info: dict = {}
        self.laya_lock = threading.Lock()

    # -- Jev ------------------------------------------------------------------
    def jev(self, prompt: str, questions: dict) -> dict:
        env = dict(os.environ)
        if not env.get("UE_KEY"):
            kf = Path("~/.ue_key").expanduser()
            if not kf.exists():
                return {"ok": False, "error": "no Jev key: set UE_KEY or create ~/.ue_key"}
            env["UE_KEY"] = kf.read_text().strip()
        p = subprocess.run([sys.executable, "-c", JEV_SCRIPT, self.tools], input=json.dumps(
            {"prompt": prompt, "questions": questions}), capture_output=True, text=True, env=env,
            cwd=self.tools, timeout=90)
        return self._parse(p.stdout, p.stderr)

    # -- Laya -----------------------------------------------------------------
    def _laya_proc(self):
        if self.laya is not None and self.laya.poll() is None:
            return self.laya
        py = self.repo / LAYA_PY
        if not py.exists():
            raise RuntimeError(f"Laya venv not found: {py}")
        env = dict(os.environ, USE_TF="0", HF_HUB_OFFLINE="1")
        self.laya = subprocess.Popen([str(py), "-c", LAYA_SCRIPT, self.tools], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     env=env, cwd=self.tools, bufsize=1)
        first = self.laya.stdout.readline()
        if not first:
            self.laya = None
            raise RuntimeError("Laya failed to load")
        self.laya_info = json.loads(first)
        return self.laya

    def laya_ask(self, prompt: str, kind: str, options: list, questions: dict | None) -> dict:
        with self.laya_lock:
            try:
                proc = self._laya_proc()
                proc.stdin.write(json.dumps({"prompt": prompt, "kind": kind, "options": options, "questions": questions}) + "\n")
                proc.stdin.flush()
                out = self._parse(proc.stdout.readline(), "")
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if self.laya_info.get("load_s"):
            out["load_s"] = self.laya_info.pop("load_s")
        return out

    def close(self):
        if self.laya is not None and self.laya.poll() is None:
            self.laya.kill()

    @staticmethod
    def _parse(stdout: str, stderr: str) -> dict:
        try:
            out = json.loads((stdout or "").strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return {"ok": False, "error": (stderr or stdout or "no output").strip()[-400:]}
        out.update(_first(out.get("answers")))
        return out

    # -- entry point ------------------------------------------------------------
    def ask(self, model: str, res: dict, prompt: str | None = None, questions: dict | None = None) -> dict:
        """Same prompt + questions to either model; ``prompt`` / ``questions`` override the logged ones."""
        io, e = res.get("io") or {}, res.get("entry") or {}
        prompt = prompt or io.get("prompt")
        if not prompt:
            return {"ok": False, "error": "this question has no prompt to send"}
        qs = questions or _questions(io.get("request"))
        if model == "jev":
            if not qs:
                return {"ok": False, "error": "no question spec in the request"}
            out = self.jev(prompt, qs)
        elif model == "laya":
            out = self.laya_ask(prompt, e.get("kind"), list(e.get("options") or []), qs)
        else:
            return {"ok": False, "error": f"unknown model {model!r}"}
        out["asked"] = model
        out.pop("answers", None)
        return out
