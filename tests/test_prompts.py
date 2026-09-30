"""L4: In -> out.  Exact prompts of L3 runs; old runs' prompts rebuilt from the Q&A files and
code as of the run's commit (git archive into a temp cache; the real tree is never written)."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import fixture
from fixture import L3_RUN, REAL_REPO, REAL_RUN, real_config

from stlab import code, config, prompts, runs


class OldRunRebuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = Path(tempfile.mkdtemp(prefix="lab-cache-"))
        cls.cfg = real_config(cls.cache)
        cls.model = runs.RunModel("results/phase12c2-jev-live", REAL_REPO / REAL_RUN)
        cls.model.refresh()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.cache, ignore_errors=True)

    def test_rebuilt_at_the_runs_commit_matches_logged_length(self):
        rows = [r for r in self.model.rows if r["kind"] == "play"]
        res = prompts.rebuild(self.cfg, self.model, rows)
        self.assertEqual(len(res), 114)
        for r, b in zip(rows, res):
            self.assertEqual(b["mode"], "rebuilt")
            self.assertEqual(b["sha"], "f9b1ced7bc")
            self.assertTrue(b["ok"], b.get("error"))
            if isinstance((r.get("meta") or {}).get("chars"), int):
                self.assertEqual(b["chars_rebuilt"], r["meta"]["chars"], r["qid"])
        first = res[0]
        self.assertTrue(first["prompt"].startswith("We are player 1.\nOur civilisation, civs/spart.json:"))
        # As of f9b1ced7bc the civ's heroOrder was still in the prompt (12d-2 stripped it
        # later): the rebuild shows what THAT game sent, not today's files.
        self.assertIn("heroOrder", first["prompt"])
        self.assertIn("Our hero, heroes/spart/agis.json:", first["prompt"])
        self.assertEqual(first["request"]["response_format"]["type"], "questions")
        self.assertEqual(set(first["request"]["response_format"]["questions"]["play"]["criteria"]),
                         set(rows[0]["options"]))
        self.assertTrue(any("length check" in n and "same length" in n for n in first["notes"]))

    def test_snapshot_is_in_the_cache_not_the_repo(self):
        d = prompts.snapshot_dir(self.cfg, "f9b1ced7bc")
        self.assertTrue(str(d).startswith(str(self.cache)))
        self.assertTrue((d / config.TOOLS_DIR / "advisor_jev.py").exists())

    def test_in_out_old_run(self):
        idx = next(r["_idx"] for r in self.model.rows if r["kind"] == "play" and r["qid"] == 723)
        res = prompts.in_out(self.cfg, self.model, idx, code.load(self.cfg))
        io = res["io"]
        self.assertEqual(io["mode"], "rebuilt")
        self.assertIsNone(io["raw_reply"])
        self.assertIn("probabilities", io["raw_reply_note"])
        self.assertEqual(res["entry"]["game"]["applied"]["order"], "hero/train")

    def test_laya_run(self):
        m = runs.RunModel("results/phase12c1-laya-live", REAL_REPO / ".claude/strategos/results/phase12c1-laya-live")
        m.refresh()
        rows = [r for r in m.rows if r["kind"] == "pass_order"][:8]
        for r, b in zip(rows, prompts.rebuild(self.cfg, m, rows)):
            self.assertEqual(b["mode"], "rebuilt")
            self.assertEqual(b["chars_rebuilt"], r["meta"]["chars"])
            self.assertIn("state", b["request"])      # laya: state + questions, no HTTP body

    def test_prompt_variant_run(self):
        """phase12c1-jev-script ran with JEV_PROMPT_VARIANT=script: the rebuild applies it."""
        m = runs.RunModel("results/phase12c1-jev-script", REAL_REPO / ".claude/strategos/results/phase12c1-jev-script")
        m.refresh()
        rows = [r for r in m.rows if r["kind"] == "pass_order"][:5]
        for r, b in zip(rows, prompts.rebuild(self.cfg, m, rows)):
            self.assertEqual(b["chars_rebuilt"], r["meta"]["chars"])
            self.assertIn("Our civ script (Sparta):", b["prompt"])


@unittest.skipUnless((REAL_REPO / L3_RUN).exists(), "the real L3 run is not there")
class RealL3PromptTest(unittest.TestCase):
    def test_exact_and_rebuild_agree(self):
        cache = Path(tempfile.mkdtemp(prefix="lab-cache-"))
        try:
            cfg = real_config(cache)
            m = runs.RunModel("results/lab-l3-jev-live", REAL_REPO / L3_RUN)
            m.refresh()
            res = prompts.in_out(cfg, m, 1, code.load(cfg))
            self.assertEqual(res["io"]["mode"], "exact")
            self.assertTrue(res["io"]["raw_reply_parsed"])
            # The rebuild (commit + the run's qa_snapshot) gives the logged prompt, byte for byte.
            built = prompts.rebuild(cfg, m, m.rows)
            for r, b in zip(m.rows, built):
                self.assertEqual(b["prompt"], r["io"]["prompt"], r["qid"])
                if r["kind"] == "play":
                    self.assertNotIn("heroOrder", b["prompt"])   # 12d-2 on: stripped
                self.assertEqual(b["request"]["response_format"], r["io"]["request"])
        finally:
            shutil.rmtree(cache, ignore_errors=True)


class FixtureRebuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = fixture.make_repo()
        cls.cfg = fixture.fixture_config(cls.repo)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)
        shutil.rmtree(cls.cfg.cache_dir, ignore_errors=True)

    def _run(self, name, mod_sha):
        head = fixture.header("0" * 40, mod_sha=mod_sha)
        row = fixture.play_row(4, ["hold", "economy"], "hold", "hold", 5.0, "pass_order:enemy")
        d = fixture.write_run(self.repo / ".claude/strategos/results" / name, head, [row], [])
        m = runs.RunModel(f"results/{name}", d)
        m.refresh()
        return m

    def test_unknown_commit_falls_back_to_current_files(self):
        m = self._run("gone", "deadbeef00")
        res = prompts.in_out(self.cfg, m, 0, code.load(self.cfg))
        self.assertEqual(res["io"]["mode"], "rebuilt from current files")
        self.assertTrue(res["io"]["prompt"].startswith("We are player 1."))

    def test_known_commit(self):
        sha = fixture.git(self.repo, "rev-parse", "HEAD").strip()
        m = self._run("known", sha[:10] + "-dirty")
        res = prompts.in_out(self.cfg, m, 0, code.load(self.cfg))
        self.assertEqual(res["io"]["mode"], "rebuilt")
        self.assertTrue(any("uncommitted" in n for n in res["io"]["notes"]))

    def test_exact_io_row(self):
        d = fixture.l3_run(self.repo, "l3-exact")
        m = runs.RunModel("results/l3-exact", d)
        m.refresh()
        res = prompts.in_out(self.cfg, m, 0, code.load(self.cfg))
        self.assertEqual(res["io"]["mode"], "exact")
        self.assertEqual(res["io"]["raw_reply_parsed"]["play"]["choice"], "hold")
        # The row without io in the same run is rebuilt with the run's qa_snapshot over its commit.
        res = prompts.in_out(self.cfg, m, 3, code.load(self.cfg))
        self.assertEqual(res["io"]["mode"], "rebuilt")
        self.assertTrue(any("qa_snapshot" in n for n in res["io"]["notes"]))

    def test_repo_untouched(self):
        self.assertEqual(fixture.git(self.repo, "status", "--porcelain", "--untracked-files=no").strip(), "")


if __name__ == "__main__":
    unittest.main()
