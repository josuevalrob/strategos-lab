"""Test helpers: a throw-away 0 A.D.-shaped git repo, and synthetic runs.

Every write test runs against a fixture made here -- copies of the real
Q&A files and code files, committed in a temp dir on branch strategos/mvp1.
The real repo is only ever read.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB))

from stlab import config  # noqa: E402

REAL_REPO = Path(os.environ.get("STRATEGOS_REPO", str(config.DEFAULT_REPO))).expanduser()
REAL_RUN = ".claude/strategos/results/phase12c2-jev-live"
L3_RUN = ".claude/strategos/results/lab-l3-jev-live"


def real_config(cache: Path | None = None) -> config.Config:
    return config.Config(repo=REAL_REPO, cache_dir=cache or Path(tempfile.mkdtemp(prefix="lab-cache-")))


def git(repo: Path, *args: str, check: bool = True) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="lab-test", GIT_AUTHOR_EMAIL="lab@test",
               GIT_COMMITTER_NAME="lab-test", GIT_COMMITTER_EMAIL="lab@test")
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {args}: {p.stderr}")
    return p.stdout


def _copy(rel: str, dest: Path) -> None:
    src = REAL_REPO / rel
    out = dest / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out)


def _touch(dest: Path, rel: str) -> None:
    out = dest / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("<Entity/>\n")


def make_repo(root: Path | None = None) -> Path:
    """A fixture repo: Q&A files, code files, doctrine, civ data, template stubs."""
    root = Path(root or tempfile.mkdtemp(prefix="lab-fixture-"))
    root.mkdir(parents=True, exist_ok=True)
    for rel in config.CODE_FILES.values():
        _copy(rel, root)
    tools = REAL_REPO / config.TOOLS_DIR
    for p in tools.glob("*.py"):
        _copy(f"{config.TOOLS_DIR}/{p.name}", root)
    for p in (REAL_REPO / config.QUESTIONS_DIR).glob("*.json"):
        _copy(f"{config.QUESTIONS_DIR}/{p.name}", root)
    data = REAL_REPO / config.MOD_DATA
    for p in data.rglob("*.json"):
        _copy(str(p.relative_to(REAL_REPO)), root)
    for p in (REAL_REPO / config.PUBLIC_CIVS).glob("*.json"):
        _copy(str(p.relative_to(REAL_REPO)), root)
    # Template stubs: the files whose existence the lab checks.
    pub = REAL_REPO / config.PUBLIC_TEMPLATES
    for p in pub.glob("units/*/hero_*.xml"):
        _touch(root, str(p.relative_to(REAL_REPO)))
    for name in ("gerousia", "defense_tower", "sentry_tower"):
        for p in pub.glob(f"structures/*/{name}.xml"):
            _touch(root, str(p.relative_to(REAL_REPO)))
    for p in pub.glob("special/formations/*.xml"):
        _touch(root, str(p.relative_to(REAL_REPO)))
    mod = REAL_REPO / config.MOD_TEMPLATES
    for p in mod.rglob("*.xml"):
        _touch(root, str(p.relative_to(REAL_REPO)))
    (root / "unrelated.txt").write_text("not a Q&A file\n")
    git(root, "init", "-q")
    git(root, "checkout", "-q", "-b", config.REQUIRED_BRANCH)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root


def fixture_config(root: Path) -> config.Config:
    return config.Config(repo=root, cache_dir=root.parent / (root.name + "-cache"))


# -- synthetic runs --------------------------------------------------------------

def header(commit: str, adapter: str = "jev", version: str = "jev-1.13.0", player: int = 1,
           qa: dict | None = None, mod_sha: str | None = None) -> dict:
    h = {"header": True, "t_wall": 1790000000.0,
         "engine": {"aiPlayers": [{"id": player, "ai": "strategos-live", "civ": "spart"},
                                  {"id": 3 - player, "ai": "petra", "civ": "spart"}]},
         "advisor": "10d.1", "adapter": {"name": adapter, "version": version, "model": version},
         "specs": {}, "mod_sha": mod_sha or commit[:10]}
    if qa is not None:
        h["qa"] = qa
    return h


def play_row(qid: int, options: list, choice, rule: str, minute: float, events: str,
             player: int = 1, io: dict | None = None, outcome: str = "posted", p=0.5) -> dict:
    probs = {o: round((1 - p) / max(1, len(options) - 1), 3) for o in options}
    if choice:
        probs[choice] = p
    row = {"t_wall": 1790000000.0 + qid, "serial": qid, "player": player, "qid": qid, "kind": "play",
           "options": options,
           "features": {"game_minute": minute, "our_phase": 1, "opponent": "booming", "food": 100,
                        "wood": 100, "stone": 100, "metal": 100, "pop": 20, "pop_max": 30,
                        "events": events},
           "rule": rule, "askedTime": int(minute * 60000), "deadlineTime": int(minute * 60000) + 3400,
           "adapter": "jev", "choice": choice, "p": p if choice else None, "latency_ms": 400.0,
           "budget_s": 2.8, "outcome": outcome, "error": None,
           "meta": {"spec": "play", "probabilities": probs, "chars": 100} if choice else None}
    if io is not None:
        row["io"] = io
    return row


def engine_line(player: int, text: str) -> str:
    return f"WARNING: PlayerID {player} |   [strategos] {text}\n"


def write_run(run_dir: Path, head: dict, rows: list, engine: list[str],
              snapshot_from: Path | None = None) -> Path:
    adv = run_dir / "advisor"
    adv.mkdir(parents=True, exist_ok=True)
    with open(adv / "2026-09-30_0001.jsonl", "w") as fh:
        for obj in [head] + rows:
            fh.write(json.dumps(obj) + "\n")
    (run_dir / "engine.log").write_text("".join(engine))
    if snapshot_from is not None:
        snap = adv / "qa_snapshot"
        shutil.copytree(snapshot_from / config.QUESTIONS_DIR, snap / "questions")
        shutil.copytree(snapshot_from / config.CIVS_DIR, snap / "civs")
        shutil.copytree(snapshot_from / config.HEROES_DIR, snap / "heroes")
    return run_dir


def l3_run(repo: Path, name: str = "synthetic-l3") -> Path:
    """A run that uses every L3 contract field, in the fixture repo's results."""
    commit = git(repo, "rev-parse", "HEAD").strip()
    qa = {"commit": commit, "dirty": False, "snapshot": "qa_snapshot",
          "files": {f"{config.QUESTIONS_DIR}/play.json": "abc123def456"}}
    io = {"prompt": "We are player 1.\nNow:\nfood: 100", "request": {"type": "questions", "questions": {
        "play": {"type": "choice", "instructions": "x", "criteria": {"hold": "a", "economy": "b"}}}},
        "raw_reply": json.dumps({"play": {"choice": "hold", "probabilities": {"hold": 0.7, "economy": 0.3}}}),
        "model": "jev-1.13.0"}
    rows = [
        play_row(2, ["hold", "fallback", "economy", "train:soldiers"], "train:soldiers", "economy", 3.0,
                 "pass_order:booming+phase", io=io),
        play_row(5, ["hold", "fallback", "economy"], "hold", "hold", 4.9, "pass_order:attacking+rule", io=io),
        play_row(8, ["hold", "fallback", "economy"], "hold", "hold", 5.5, "pass_order:enemy", io=io),
        play_row(11, ["hold", "fallback", "economy"], None, "economy", 6.0, "pass_order:clear",
                 io=None, outcome="no_answer"),
        play_row(14, ["hero:hero_agis", "hero:hero_brasidas", "economy"], "hero:hero_agis", "economy", 40.0,
                 "hero_next:slot", io=io),
    ]
    eng = [
        "Turn 1 (200)...\n",
        engine_line(1, "p1 play at pass_order:booming+phase: asked q#2 options hold/fallback/economy/train:soldiers (rule economy)"),
        engine_line(1, "p1 q#2 play=train:soldiers by jev p=0.5 (answered after 5 turns, read 3 later)"),
        engine_line(1, "q#2 train:soldiers: 3 units/spart/infantry_javelineer_b"),
        engine_line(1, "p1 q#2 applied train:soldiers -> train:soldiers via queueManager"),
        engine_line(1, "p1 q#5 play=hold by jev p=0.5 (answered after 5 turns, read 3 later)"),
        engine_line(1, "q#5/pass_order hold-the-pass/hold -> defenseManager, garrisonManager: reserve 8"),
        engine_line(1, "p1 q#5 applied hold -> hold via defenseManager,garrisonManager"),
        engine_line(1, "p1 q#8 play=hold by jev p=0.5 (answered after 5 turns, read 3 later)"),
        engine_line(1, "p1 q#8 applied hold -> none via none"),
        engine_line(1, "p1 q#11 play=economy by rule (no answer in 16 turns)"),
        engine_line(1, "p1 q#14 play=hero:hero_agis by jev p=0.5 (answered after 8 turns, read 8 later)"),
        engine_line(1, "q#14/hero_next hero/train -> attackManager: structures/spart/gerousia standing; queued units/spart/hero_agis"),
        engine_line(1, "p1 q#14 applied hero:hero_agis -> train via attackManager"),
    ]
    return write_run(repo / ".claude/strategos/results" / name, header(commit, qa=qa), rows, eng,
                     snapshot_from=repo)
