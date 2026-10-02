"""Where things live in the 0 A.D. repo (all paths repo-relative unless noted)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = Path(os.path.expanduser("~/Projects/0AD"))
REQUIRED_BRANCH = "strategos/v2"

MOD_DATA = "binaries/data/mods/strategos/simulation/data/strategos"
CIVS_DIR = MOD_DATA + "/civs"
HEROES_DIR = MOD_DATA + "/heroes"
DOCTRINE_JSON = MOD_DATA + "/doctrine.json"
QUESTIONS_DIR = MOD_DATA + "/blocks"  # one params + wording file per block (shared with the game)
TOOLS_DIR = "source/tools/strategos"
AI_DIR = "binaries/data/mods/strategos/simulation/ai/strategos"
PUBLIC_TEMPLATES = "binaries/data/mods/public/simulation/templates"
MOD_TEMPLATES = "binaries/data/mods/strategos/simulation/templates"
PUBLIC_CIVS = "binaries/data/mods/public/simulation/data/civs"
RESULTS_ROOTS = (".claude/strategos/results", ".claude/strategos/runs")

# The code files the map reads (key -> repo-relative path).
CODE_FILES = {
    "head.js": AI_DIR + "/head.js",
    "hero.js": AI_DIR + "/hero.js",
    "advisor.js": AI_DIR + "/advisor.js",
    "rules.js": AI_DIR + "/rules.js",
    "doctrine.js": AI_DIR + "/doctrine.js",
    "advisor_adapters.py": TOOLS_DIR + "/advisor_adapters.py",
    "advisor_jev.py": TOOLS_DIR + "/advisor_jev.py",
    "advisor.py": TOOLS_DIR + "/advisor.py",
}

# A Q&A file the editor may write: exactly these shapes, nothing else.
_QA_PATTERNS = (
    re.compile(r"^" + re.escape(QUESTIONS_DIR) + r"/[a-z0-9_]+\.json$"),
    re.compile(r"^" + re.escape(CIVS_DIR) + r"/[a-z0-9_]+\.json$"),
    re.compile(r"^" + re.escape(HEROES_DIR) + r"/[a-z0-9_]+/[a-z0-9_]+\.json$"),
)
CIV_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")


def is_qa_path(rel: str) -> bool:
    """True for a repo-relative Q&A file path (question spec, civ file, hero file, doctrine.json)."""
    if not isinstance(rel, str) or ".." in rel or rel.startswith("/"):
        return False
    return rel == DOCTRINE_JSON or any(p.match(rel) for p in _QA_PATTERNS)


def qa_kind(rel: str) -> str | None:
    if rel == DOCTRINE_JSON:
        return "doctrine"
    if rel.startswith(QUESTIONS_DIR + "/"):
        return "question"
    if rel.startswith(CIVS_DIR + "/"):
        return "civ"
    if rel.startswith(HEROES_DIR + "/"):
        return "hero"
    return None


def advisor_dir(run_dir: Path) -> Path:
    """Where a run's advisor JSONL is: <run>/advisor/ (phase10e.py, round.sh), else the run
    directory itself (solo_village.sh: <run>/ or <run>/p<N>-<model>/)."""
    adir = Path(run_dir) / "advisor"
    return adir if adir.is_dir() else Path(run_dir)


def pid_gone(run_dir: Path) -> bool:
    """True when the run wrote engine.pid and that process is no longer running."""
    pf = Path(run_dir) / "engine.pid"
    try:
        pid = int(pf.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


@dataclass
class Config:
    repo: Path = DEFAULT_REPO
    cache_dir: Path = LAB_ROOT / ".cache"
    required_branch: str = REQUIRED_BRANCH
    # Seconds since the last write for a run without summary.json to count as live.
    live_window_s: float = 120.0
    extra_run_roots: list = field(default_factory=list)

    def __post_init__(self):
        self.repo = Path(self.repo).expanduser().resolve()
        self.cache_dir = Path(self.cache_dir)

    def path(self, rel: str) -> Path:
        return self.repo / rel

    def run_roots(self) -> list[Path]:
        return [self.repo / r for r in RESULTS_ROOTS] + [Path(p) for p in self.extra_run_roots]
