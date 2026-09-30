#!/usr/bin/env python3
"""Strategos Lab: see and edit how Jev / Laya are asked, and what happened in games.

    python3 lab.py [--repo ~/Projects/0AD] [--port 8765]

Serves http://127.0.0.1:<port>/ (never another interface).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stlab import config, gitops  # noqa: E402
from stlab.server import make_server  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=str(config.DEFAULT_REPO), help="the 0 A.D. repo")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--cache", default=str(config.LAB_ROOT / ".cache"),
                    help="where rebuilt-prompt snapshots are kept")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    args = ap.parse_args(argv)
    cfg = config.Config(repo=Path(args.repo), cache_dir=Path(args.cache))
    if not (cfg.repo / ".git").exists():
        print(f"not a git repo: {cfg.repo}", file=sys.stderr)
        return 2
    srv = make_server(cfg, args.port, quiet=not args.verbose)
    br = gitops.branch(cfg.repo)
    print(f"Strategos Lab on http://127.0.0.1:{srv.server_address[1]}/  (repo {cfg.repo}, branch {br})",
          flush=True)
    if br != cfg.required_branch:
        print(f"note: Apply is refused until the repo is on {cfg.required_branch}", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
