"""L5: edit -> Apply = write + commit only that file; refusals; history, diff, restore.
Everything here runs on a FIXTURE repo (tests/fixture.py), never the real one."""

import json
import shutil
import subprocess
import sys
import unittest

import fixture
from fixture import git

from stlab import code, config, editor, gitops, qa

PLAY = f"{config.QUESTIONS_DIR}/play.json"
RAID = f"{config.QUESTIONS_DIR}/raid.json"
CIV = f"{config.CIVS_DIR}/spart.json"
AGIS = f"{config.HEROES_DIR}/spart/agis.json"


class EditorTest(unittest.TestCase):
    def setUp(self):
        self.repo = fixture.make_repo()
        self.cfg = fixture.fixture_config(self.repo)
        self.facts = code.load(self.cfg)
        self.first = git(self.repo, "rev-parse", "HEAD").strip()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.cfg.cache_dir, ignore_errors=True)

    def play(self):
        return json.loads((self.repo / PLAY).read_text())

    def edit_play(self, text="send no order now: Petra's economy runs"):
        d = self.play()
        d["questions"]["play"]["criteria"]["economy"] = text
        return d

    def base(self, rel):
        return qa.sha256((self.repo / rel).read_text())

    # -- apply ---------------------------------------------------------------
    def test_apply_commits_only_that_file(self):
        (self.repo / "unrelated.txt").write_text("dirty, never committed by the lab\n")
        raid = json.loads((self.repo / RAID).read_text())
        raid["v"] = 99
        (self.repo / RAID).write_text(json.dumps(raid, indent=1) + "\n")
        git(self.repo, "add", RAID)                      # someone else's staged change
        res = editor.apply(self.cfg, self.facts, [{"path": PLAY, "data": self.edit_play(),
                                                   "base_sha256": self.base(PLAY)}], "economy wording")
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s").strip(), "Strategos lab: economy wording")
        self.assertEqual(res["files"], [PLAY])
        self.assertEqual(git(self.repo, "show", "--name-only", "--format=", "HEAD").split(), [PLAY])
        status = git(self.repo, "status", "--porcelain", "--untracked-files=no")
        self.assertIn(" M unrelated.txt", status)
        self.assertIn("M  " + RAID, status)               # still staged, not committed
        self.assertNotIn(PLAY, status)
        text = (self.repo / PLAY).read_text()
        self.assertIn("\n \"v\": 1,", text)              # the file's own 1-space style kept
        self.assertEqual(json.loads(text)["questions"]["play"]["criteria"]["economy"],
                         "send no order now: Petra's economy runs")
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD").strip(), "2")

    def test_raw_text_apply_and_tab_style(self):
        d = json.loads((self.repo / AGIS).read_text())
        d["text"].append("A new line about Agis.")
        editor.apply(self.cfg, self.facts, [{"path": AGIS, "data": d}], "")
        text = (self.repo / AGIS).read_text()
        self.assertIn('\n\t"hero": "Agis III",', text)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s").strip(), "Strategos lab: edit agis.json")
        raw = text.replace("A new line about Agis.", "Another line.")
        editor.apply(self.cfg, self.facts, [{"path": AGIS, "text": raw}], "raw edit")
        self.assertEqual((self.repo / AGIS).read_text(), raw)

    def test_browser_numbers_keep_the_files_spelling(self):
        """The page parses 1.0 as 1; Apply must still write 1.0 where the file had it."""
        rel = f"{config.HEROES_DIR}/spart/brasidas.json"
        self.assertIn('"strikeRatio": 1.0', (self.repo / rel).read_text())

        def as_js(v):
            if isinstance(v, float) and v.is_integer():
                return int(v)
            if isinstance(v, dict):
                return {k: as_js(x) for k, x in v.items()}
            if isinstance(v, list):
                return [as_js(x) for x in v]
            return v
        d = as_js(json.loads((self.repo / rel).read_text()))
        d["battle"] = "Amphipolis (422 BC)"
        editor.apply(self.cfg, self.facts, [{"path": rel, "data": d}], "battle")
        diff = git(self.repo, "show", "--format=", "HEAD", "--", rel)
        self.assertEqual([l for l in diff.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---")],
                         ['-\t"battle": "Amphipolis, 422 BC",', '+\t"battle": "Amphipolis (422 BC)",'])
        self.assertEqual(qa.keep_number_types({"a": 1, "b": [2, 3.5], "c": True}, {"a": 1.0, "b": [2.0, 3.5], "c": 1}),
                         {"a": 1.0, "b": [2.0, 3.5], "c": True})

    def test_refuses_other_branch(self):
        git(self.repo, "checkout", "-q", "-b", "main")
        before = (self.repo / PLAY).read_text()
        with self.assertRaises(editor.Refused) as cm:
            editor.apply(self.cfg, self.facts, [{"path": PLAY, "data": self.edit_play()}], "x")
        self.assertEqual(cm.exception.code, "branch")
        self.assertEqual((self.repo / PLAY).read_text(), before)
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD").strip(), "1")

    def test_dirty_target_needs_confirmation(self):
        d = self.play()
        d["questions"]["play"]["criteria"]["hold"] = "edited outside the lab"
        (self.repo / PLAY).write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
        change = {"path": PLAY, "data": self.edit_play(), "base_sha256": self.base(PLAY)}
        with self.assertRaises(editor.Refused) as cm:
            editor.apply(self.cfg, self.facts, [change], "x")
        self.assertEqual(cm.exception.code, "dirty")
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD").strip(), "1")
        res = editor.apply(self.cfg, self.facts, [change], "x", confirm_dirty=True)
        self.assertEqual(res["dirty_before"], {PLAY: " M"})
        self.assertEqual(git(self.repo, "status", "--porcelain", "--", PLAY).strip(), "")

    def test_changed_on_disk(self):
        with self.assertRaises(editor.Refused) as cm:
            editor.apply(self.cfg, self.facts, [{"path": PLAY, "data": self.edit_play(),
                                                 "base_sha256": "0" * 64}], "x")
        self.assertEqual(cm.exception.code, "changed_on_disk")

    def test_validation_refuses_impossible_options(self):
        d = self.play()
        d["questions"]["play"]["criteria"]["charge"] = "attack everything"
        with self.assertRaises(editor.Refused) as cm:
            editor.apply(self.cfg, self.facts, [{"path": PLAY, "data": d}], "x")
        self.assertEqual(cm.exception.code, "validation")
        self.assertIn("charge", cm.exception.message)
        civ = json.loads((self.repo / CIV).read_text())
        civ["heroes"].append("xerxes")
        civ["params"]["doctrine"] = "blitz"
        res = editor.validate(self.cfg, self.facts, CIV, json.dumps(civ))
        self.assertTrue(any("xerxes" in e for e in res["errors"]))
        self.assertTrue(any("blitz" in e for e in res["errors"]))
        hero = json.loads((self.repo / AGIS).read_text())
        hero["templates"] = ["units/spart/hero_nobody"]
        res = editor.validate(self.cfg, self.facts, AGIS, json.dumps(hero))
        self.assertTrue(any("hero_nobody" in e for e in res["errors"]))
        self.assertEqual(editor.validate(self.cfg, self.facts, PLAY, "{not json")["errors"][0][:12], "invalid JSON")
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD").strip(), "1")

    def test_validation_never_picks(self):
        """Dropping a possible option's wording is only a warning: the model still decides."""
        d = self.play()
        del d["questions"]["play"]["criteria"]["strike"]
        res = editor.validate(self.cfg, self.facts, PLAY, json.dumps(d))
        self.assertEqual(res["errors"], [])
        self.assertTrue(any("strike" in w for w in res["warnings"]))
        for rel in [PLAY, CIV, AGIS] + [f"{config.QUESTIONS_DIR}/{n}.json" for n in
                                         ("ambush", "garrison_now", "pass_order", "raid", "tower_site", "wall_now")]:
            with self.subTest(file=rel):
                self.assertEqual(editor.validate(self.cfg, self.facts, rel, (self.repo / rel).read_text())["errors"], [])

    def test_old_problem_does_not_block_an_unrelated_edit(self):
        d = self.play()
        d["questions"]["play"]["criteria"]["charge"] = "old mistake"
        (self.repo / PLAY).write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
        git(self.repo, "commit", "-qam", "an old mistake")
        d["questions"]["play"]["criteria"]["economy"] = "new wording"
        res = editor.apply(self.cfg, self.facts, [{"path": PLAY, "data": d}], "x")
        self.assertTrue(any("already in the file" in w for w in res["warnings"]))

    def test_other_refusals(self):
        cases = [([{"path": "unrelated.txt", "text": "x"}], "path"),
                 ([{"path": "../etc/passwd", "text": "x"}], "path"),
                 ([{"path": f"{config.HEROES_DIR}/spart/newhero.json", "text": "{}"}], "new_file"),
                 ([{"path": PLAY, "text": (self.repo / PLAY).read_text()}], "no_change"),
                 ([], "bad_request")]
        for changes, code_ in cases:
            with self.subTest(code=code_):
                with self.assertRaises(editor.Refused) as cm:
                    editor.apply(self.cfg, self.facts, changes, "x")
                self.assertEqual(cm.exception.code, code_)
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD").strip(), "1")

    # -- history / diff / restore -------------------------------------------------
    def test_history_diff_restore(self):
        original = (self.repo / PLAY).read_text()
        res = editor.apply(self.cfg, self.facts, [{"path": PLAY, "data": self.edit_play()}], "wording 1")
        edited = (self.repo / PLAY).read_text()
        log = editor.history(self.cfg, PLAY)
        self.assertEqual([c["subject"] for c in log], ["Strategos lab: wording 1", "fixture"])
        d = editor.diff(self.cfg, PLAY, self.first, res["commit"])
        was = json.loads(original)["questions"]["play"]["criteria"]["economy"]
        self.assertIn("-    \"economy\": " + json.dumps(was, ensure_ascii=False) + ",", d["diff"])
        self.assertIn("+    \"economy\": \"send no order now: Petra's economy runs\",", d["diff"])
        self.assertTrue(editor.diff(self.cfg, PLAY, res["commit"], "working")["same"])
        dd = editor.diff(self.cfg, PLAY, "working", "draft", draft=original)
        self.assertFalse(dd["same"])
        r2 = editor.restore(self.cfg, self.facts, PLAY, self.first)
        self.assertEqual((self.repo / PLAY).read_text(), original)
        self.assertEqual(r2["files"], [PLAY])
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s").strip(),
                         f"Strategos lab: restore play.json to {self.first[:10]}")
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip(), config.REQUIRED_BRANCH)
        self.assertEqual(len(editor.history(self.cfg, PLAY)), 3)
        self.assertNotEqual(edited, original)
        with self.assertRaises(editor.Refused):
            editor.restore(self.cfg, self.facts, PLAY, "not-a-sha")

    def test_next_games_header_shows_the_new_commit(self):
        """The L3 advisor records HEAD at game start: after Apply that is the lab's commit."""
        tools = self.repo / config.TOOLS_DIR
        if "def snapshot_qa" not in (tools / "advisor_adapters.py").read_text():
            self.skipTest("advisor_adapters.snapshot_qa (lab L3) not in this repo")
        res = editor.apply(self.cfg, self.facts, [{"path": PLAY, "data": self.edit_play()}], "for the next game")
        snap = self.cfg.cache_dir / "game-snapshot"
        script = ("import json,sys; sys.path.insert(0,'.'); import advisor_adapters as A; "
                  f"print(json.dumps(A.snapshot_qa(__import__('pathlib').Path({str(snap)!r}))))")
        out = subprocess.run([sys.executable, "-c", script], cwd=tools, capture_output=True, text=True,
                             env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
        self.assertEqual(out.returncode, 0, out.stderr)
        header_qa = json.loads(out.stdout)
        self.assertEqual(header_qa["commit"], res["commit"])
        self.assertFalse(header_qa["dirty"])
        self.assertEqual(header_qa["files"][PLAY], qa.sha256((self.repo / PLAY).read_bytes())[:12])


class GitOpsTest(unittest.TestCase):
    def test_status_and_show(self):
        repo = fixture.make_repo()
        try:
            self.assertEqual(gitops.branch(repo), config.REQUIRED_BRANCH)
            self.assertEqual(gitops.status_of(repo, [PLAY]), {})
            (repo / PLAY).write_text("{}\n")
            self.assertEqual(gitops.status_of(repo, [PLAY]), {PLAY: " M"})
            self.assertIn(b'"stratagem": "play"', gitops.show(repo, "HEAD", PLAY))
            self.assertIsNone(gitops.show(repo, "HEAD", "nope.json"))
        finally:
            shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
