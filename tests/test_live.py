"""L6: live tail.  A fake game writes the advisor log and engine.log line by line (with
partial lines); the lab picks up new questions incrementally, well within 5 s."""

import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import fixture

from stlab import config, runs, server


class FakeGame:
    """Appends a header, then one play question every `every` seconds, in two chunks per line."""

    def __init__(self, run_dir: Path, n: int = 4, every: float = 0.3):
        self.dir = run_dir
        self.n, self.every = n, every
        self.written: dict[int, float] = {}
        (run_dir / "advisor").mkdir(parents=True, exist_ok=True)
        self.jsonl = run_dir / "advisor" / "2026-09-30_0009.jsonl"
        self.engine = run_dir / "engine.log"
        self.thread = threading.Thread(target=self.play, daemon=True)

    def _append(self, path: Path, text: str, split: bool = True) -> None:
        cut = len(text) // 2 if split else len(text)
        with open(path, "a") as fh:
            fh.write(text[:cut])
            fh.flush()
        time.sleep(0.02)
        with open(path, "a") as fh:
            fh.write(text[cut:])

    def play(self) -> None:
        self._append(self.jsonl, json.dumps(fixture.header("f" * 40)) + "\n")
        self._append(self.engine, "GAME STARTED\n")
        for i in range(self.n):
            qid = 10 + 3 * i
            row = fixture.play_row(qid, ["hold", "economy"], "economy", "hold", 3.0 + i, "pass_order:booming")
            self._append(self.engine, fixture.engine_line(
                1, f"p1 play at pass_order:booming: asked q#{qid} options hold/economy (rule hold)"))
            self._append(self.jsonl, json.dumps(row) + "\n")
            self.written[qid] = time.time()
            time.sleep(self.every / 2)
            self._append(self.engine, fixture.engine_line(1, f"p1 q#{qid} play=economy by jev p=0.5 (x)"))
            self._append(self.engine, fixture.engine_line(1, f"p1 q#{qid} applied economy -> none via none"))
            time.sleep(self.every / 2)


class RunModelTailTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lab-live-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_incremental_tail(self):
        game = FakeGame(self.root / "results" / "live1", n=4, every=0.3)
        m = runs.RunModel("results/live1", game.dir)
        game.thread.start()
        seen_at, rev, updates = {}, 0, 0
        deadline = time.time() + 10
        while time.time() < deadline:
            m.refresh()
            new = m.timeline("play", since_rev=rev)
            for e in new:
                seen_at.setdefault(e["qid"], time.time())
                updates += 1
            rev = m.rev
            if len(seen_at) == 4 and all(e["game"]["applied"].get("source") == "l3" for e in m.timeline("play")):
                break
            time.sleep(0.1)
        game.thread.join(5)
        self.assertEqual(sorted(seen_at), [10, 13, 16, 19])
        for qid, t in seen_at.items():
            self.assertLess(t - game.written[qid], 5.0, qid)
        self.assertGreater(updates, 4)          # rows came back again when their engine lines arrived
        tl = m.timeline("play")
        self.assertEqual([e["qid"] for e in tl], [10, 13, 16, 19])
        self.assertTrue(all(e["game"]["settled"]["choice"] == "economy" for e in tl))
        self.assertEqual(m.timeline("play", since_rev=m.rev), [])
        self.assertEqual(len(m.headers), 1)
        self.assertTrue(m.is_live(60))

    def test_partial_line_waits(self):
        d = self.root / "results" / "p"
        (d / "advisor").mkdir(parents=True)
        f = d / "advisor" / "x.jsonl"
        line = json.dumps(fixture.header("a" * 40)) + "\n" + json.dumps(
            fixture.play_row(1, ["economy"], "economy", "economy", 3.0, ""))
        f.write_text(line[:-10])
        m = runs.RunModel("results/p", d)
        m.refresh()
        self.assertEqual(len(m.rows), 0)
        with open(f, "a") as fh:
            fh.write(line[-10:] + "\n")
        self.assertTrue(m.refresh())
        self.assertEqual(len(m.rows), 1)

    def test_rewritten_file_is_read_again(self):
        d = self.root / "results" / "r"
        fixture.write_run(d, fixture.header("a" * 40), [], [fixture.engine_line(1, "p1 q#1 play=economy by jev p=0.5 (x)")])
        m = runs.RunModel("results/r", d)
        m.refresh()
        self.assertEqual(m.engine.get(1, 1)["settled"]["choice"], "economy")
        (d / "engine.log").write_text(fixture.engine_line(1, "p1 q#2 play=hold by jev p=0.5 (x)"))
        self.assertTrue(m.refresh())
        self.assertIsNone(m.engine.get(1, 1))
        self.assertEqual(m.engine.get(1, 2)["settled"]["choice"], "hold")
        self.assertEqual(len(m.headers), 1)


class LiveApiTest(unittest.TestCase):
    """GET /api/live?run=newest through the real server; a client polling every 0.5 s."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lab-liveapi-"))
        (self.root / "repo").mkdir()
        fixture.git(self.root / "repo", "init", "-q")
        old = self.root / "results" / "old"
        fixture.write_run(old, fixture.header("a" * 40), [fixture.play_row(1, ["economy"], "economy", "economy", 3, "")], [])
        (old / "summary.json").write_text("{}")
        cfg = config.Config(repo=self.root / "repo", cache_dir=self.root / "cache",
                            extra_run_roots=[str(self.root / "results")])
        self.srv = server.make_server(cfg, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return json.loads(r.read())

    def test_new_game_shows_within_5s(self):
        time.sleep(0.05)
        game = FakeGame(self.root / "results" / "new", n=3, every=0.4)
        game.thread.start()
        seen, rev, rid = {}, 0, None
        deadline = time.time() + 12
        while time.time() < deadline and len(seen) < 3:
            res = self.get(f"/api/live?run=newest&kind=play&since={rev}")
            if res["run"]["id"] != rid:
                rid, rev = res["run"]["id"], 0
                continue
            for e in res["entries"]:
                seen.setdefault(e["qid"], time.time())
            rev = res["rev"]
            time.sleep(0.5)
        game.thread.join(5)
        self.assertEqual(rid, "results/new")
        self.assertEqual(sorted(seen), [10, 13, 16])
        for qid, t in seen.items():
            self.assertLess(t - game.written[qid], 5.0)
        info = self.get("/api/info")
        self.assertIn("results/new", info["live_runs"])
        self.assertIn("may already use an edit", info["live_note"])     # the fake game has no qa pin


if __name__ == "__main__":
    unittest.main()
