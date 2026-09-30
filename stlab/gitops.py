"""Thin git helpers for the 0 A.D. repo.

Read commands run with ``--no-optional-locks`` so looking never rewrites the
index.  The only writing command is :func:`commit_only`, used by the editor.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def git(repo: Path, *args: str, input: bytes | None = None, check: bool = True,
        text: bool = True, timeout: float = 60.0, read_only: bool = True):
    cmd = ["git", "-C", str(repo)]
    if read_only:
        cmd.append("--no-optional-locks")
    cmd += list(args)
    proc = subprocess.run(cmd, input=input, capture_output=True, timeout=timeout, env=_env())
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args[:3])}: {err or 'exit ' + str(proc.returncode)}")
    if text:
        return proc.stdout.decode("utf-8", "replace")
    return proc.stdout


def branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()


def head(repo: Path, short: bool = False) -> str:
    args = ["rev-parse", "--short=10", "HEAD"] if short else ["rev-parse", "HEAD"]
    return git(repo, *args, check=False).strip()


def resolve(repo: Path, rev: str) -> str | None:
    out = git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}", check=False).strip()
    return out or None


def status_of(repo: Path, paths: list[str]) -> dict[str, str]:
    """``{path: two-letter porcelain status}`` for the paths that are not clean."""
    if not paths:
        return {}
    out = git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--", *paths)
    res = {}
    for line in out.splitlines():
        if len(line) > 3:
            res[line[3:].strip().strip('"')] = line[:2]
    return res


def show(repo: Path, rev: str, rel: str) -> bytes | None:
    try:
        return git(repo, "show", f"{rev}:{rel}", text=False)
    except GitError:
        return None


def is_tracked(repo: Path, rel: str) -> bool:
    return bool(git(repo, "ls-files", "--", rel, check=False).strip())


def log(repo: Path, rel: str, limit: int = 60) -> list[dict]:
    fmt = "%H%x1f%h%x1f%an%x1f%aI%x1f%s"
    out = git(repo, "log", f"-n{int(limit)}", f"--format={fmt}", "--follow", "--", rel, check=False)
    rows = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            rows.append({"sha": parts[0], "short": parts[1], "author": parts[2],
                         "date": parts[3], "subject": parts[4]})
    return rows


def archive(repo: Path, rev: str, paths: list[str]) -> bytes:
    return git(repo, "archive", "--format=tar", rev, "--", *paths, text=False, timeout=120)


def commit_only(repo: Path, paths: list[str], message: str) -> str:
    """Commit exactly ``paths`` (their working-tree content), nothing else.

    ``git commit --only`` ignores whatever else is staged; new files are
    added first because ``--only`` needs git to know the path.
    """
    new = [p for p in paths if not is_tracked(repo, p)]
    if new:
        git(repo, "add", "--", *new, read_only=False)
    git(repo, "commit", "--only", "-q", "-m", message, "--", *paths,
        read_only=False)
    return head(repo)


def changed_in(repo: Path, sha: str) -> list[str]:
    out = git(repo, "show", "--name-only", "--format=", sha, check=False)
    return [line for line in out.splitlines() if line.strip()]
