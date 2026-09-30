"""doctrine.json in the editor: the stratagems' order lists are the military play options.
Everything runs on a FIXTURE repo."""

import json
import shutil
import subprocess
import sys
import unittest

import fixture
from fixture import git

from stlab import code, config, editor, mapgraph, qa

DOC = config.DOCTRINE_JSON


def as_js(v):
    """What the page sends back: JSON.parse turns 3.0 into 3."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, dict):
        return {k: as_js(x) for k, x in v.items()}
    if isinstance(v, list):
        return [as_js(x) for x in v]
    return v


class DoctrineTest(unittest.TestCase):
    def setUp(self):
        self.repo = fixture.make_repo()
        self.cfg = fixture.fixture_config(self.repo)
        self.facts = code.load(self.cfg)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.cfg.cache_dir, ignore_errors=True)

    def doc(self):
        return json.loads((self.repo / DOC).read_text())

    def check(self, d):
        return editor.validate(self.cfg, self.facts, DOC, json.dumps(d))

    def test_is_a_qa_file(self):
        self.assertTrue(config.is_qa_path(DOC))
        self.assertEqual(config.qa_kind(DOC), "doctrine")
        self.assertEqual(qa.list_files(self.cfg)["doctrine"], [{"name": "doctrine", "path": DOC}])
        self.assertEqual(self.check(self.doc())["errors"], [])

    def test_impossible_orders_are_refused(self):
        cases = {
            "not in STRATAGEM_ORDERS": (lambda d: d["stratagems"]["hold-the-pass"]["orders"].append("trade"),
                                        "does not let hold-the-pass issue 'trade'"),
            "no such order": (lambda d: d["stratagems"]["fortify"]["orders"].append("charge"),
                              "order 'charge' does not exist"),
            "twice": (lambda d: d["stratagems"]["ambush"]["orders"].append("hold"), "listed twice"),
            "unknown stratagem": (lambda d: d["stratagems"].__setitem__("blitz", {"orders": ["strike"]}),
                                  "head.js has no stratagem 'blitz'"),
            "stratagem still played": (lambda d: d["stratagems"].pop("fortify"),
                                       "'fortify' is played by spart"),
        }
        for name, (edit, needle) in cases.items():
            with self.subTest(case=name):
                d = self.doc()
                edit(d)
                errs = self.check(d)["errors"]
                self.assertTrue(any(needle in e for e in errs), errs)
                with self.assertRaises(editor.Refused) as cm:
                    editor.apply(self.cfg, self.facts, [{"path": DOC, "data": d}], "x")
                self.assertEqual(cm.exception.code, "validation")
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD").strip(), "1")

    def test_never_picks(self):
        """Dropping or reordering orders is the author's call: no error."""
        d = self.doc()
        d["stratagems"]["hold-the-pass"]["orders"] = ["fallback", "hold"]
        d["stratagems"]["fortify"]["orders"] = []
        res = self.check(d)
        self.assertEqual(res["errors"], [])
        self.assertTrue(any("only economy" in w for w in res["warnings"]))

    def test_apply_is_a_one_line_commit_of_doctrine_only(self):
        (self.repo / "unrelated.txt").write_text("dirty\n")
        before = (self.repo / DOC).read_text()
        d = as_js(self.doc())
        d["stratagems"]["hold-the-pass"]["orders"] = ["hold", "fallback"]
        res = editor.apply(self.cfg, self.facts, [{"path": DOC, "data": d, "base_sha256": qa.sha256(before)}],
                           "no strike for hold-the-pass")
        self.assertEqual(res["files"], [DOC])
        self.assertEqual(git(self.repo, "show", "--name-only", "--format=", "HEAD").split(), [DOC])
        diff = git(self.repo, "show", "--format=", "HEAD")
        changed = [l for l in diff.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        self.assertEqual(changed, ['-\t\t\t"orders": ["hold", "strike", "fallback"],',
                                   '+\t\t\t"orders": ["hold", "fallback"],'])
        self.assertIn(" M unrelated.txt", git(self.repo, "status", "--porcelain", "--untracked-files=no"))
        # History, diff, restore: same as every other file.
        log = editor.history(self.cfg, DOC)
        self.assertEqual(log[0]["subject"], "Strategos lab: no strike for hold-the-pass")
        self.assertIn('+\t\t\t"orders": ["hold", "fallback"],',
                      editor.diff(self.cfg, DOC, log[1]["sha"], log[0]["sha"])["diff"])
        editor.restore(self.cfg, self.facts, DOC, log[1]["sha"])
        self.assertEqual((self.repo / DOC).read_text(), before)
        self.assertEqual(len(editor.history(self.cfg, DOC)), 3)

    def test_branch_refusal(self):
        git(self.repo, "checkout", "-q", "-b", "other")
        d = self.doc()
        d["stratagems"]["hold-the-pass"]["orders"] = ["hold"]
        with self.assertRaises(editor.Refused) as cm:
            editor.apply(self.cfg, self.facts, [{"path": DOC, "data": d}], "x")
        self.assertEqual(cm.exception.code, "branch")

    def test_map_follows_the_order_lists(self):
        g = mapgraph.build(self.cfg, "spart")
        n = {x["id"]: x for x in g["nodes"]}
        hold = n["opt:hold"]
        self.assertIn("doctrine.json", hold["label"])
        srcs = hold["details"]["sources"]
        self.assertEqual([s["focus"] for s in srcs], ["stratagem:hold-the-pass", "stratagem:ambush"])
        self.assertTrue(all(s["path"] == DOC and isinstance(s["line"], int) for s in srcs))
        line = (self.repo / DOC).read_text().splitlines()[srcs[0]["line"] - 1]
        self.assertIn('"orders": ["hold", "strike", "fallback"]', line)
        self.assertTrue(n["part:pass_order"]["details"]["sources"])
        # Drop strike from both pass stratagems: the option leaves the map.
        d = as_js(self.doc())
        for k in ("hold-the-pass", "ambush"):
            d["stratagems"][k]["orders"] = ["hold", "fallback"]
        editor.apply(self.cfg, self.facts, [{"path": DOC, "data": d}], "no strike")
        ids = {x["id"] for x in mapgraph.build(self.cfg, "spart")["nodes"]}
        self.assertNotIn("opt:strike", ids)
        self.assertIn("opt:hold", ids)

    def test_live_note_names_doctrine(self):
        d = self.doc()
        d["stratagems"]["hold-the-pass"]["orders"] = ["hold"]
        res = editor.apply(self.cfg, self.facts, [{"path": DOC, "data": d}], "x", live_runs=["results/x"])
        self.assertIn("doctrine.json is read once by head.js at game start", res["live_note"])
        self.assertIn("qa_snapshot (lab L3) does not copy it", res["live_note"])

    def test_qa_snapshot_does_not_include_doctrine(self):
        """Documents the L3 finding the README states: the advisor snapshots questions/,
        civs/ and heroes/ only."""
        tools = self.repo / config.TOOLS_DIR
        if "def snapshot_qa" not in (tools / "advisor_adapters.py").read_text():
            self.skipTest("no lab L3 in this repo")
        snap = self.cfg.cache_dir / "snap"
        script = ("import json,sys,pathlib; sys.path.insert(0,'.'); import advisor_adapters as A; "
                  f"print(json.dumps(A.snapshot_qa(pathlib.Path({str(snap)!r}))))")
        out = subprocess.run([sys.executable, "-c", script], cwd=tools, capture_output=True, text=True,
                             env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
        self.assertEqual(out.returncode, 0, out.stderr)
        files = json.loads(out.stdout)["files"]
        self.assertIn(f"{config.CIVS_DIR}/spart.json", files)
        self.assertNotIn(DOC, files)
        self.assertEqual(sorted(p.name for p in snap.iterdir()), ["civs", "heroes", "questions"])


class PatchTextTest(unittest.TestCase):
    def test_hand_formatting_survives(self):
        text = '{\n\t"a": { "x": 1, "y": 2 },\n\n\t"b": ["p", "q"],\n\t"c": {\n\t\t"k": 1.0\n\t}\n}\n'
        self.assertEqual(qa.dump_data(json.loads(text), text), text)
        d = json.loads(text)
        d["b"] = ["q"]
        d["c"]["k"] = 1          # the browser's 1.0
        self.assertEqual(qa.dump_data(d, text), text.replace('["p", "q"]', '["q"]'))
        d["c"] = {"k": 2, "new": True}          # keys changed: the object is rewritten, still valid
        out = qa.dump_data(d, text)
        self.assertEqual(json.loads(out), d)
        self.assertIn('\t"a": { "x": 1, "y": 2 },\n\n', out)

    def test_line_of(self):
        text = '{\n\t"s": {\n\t\t"k": {\n\t\t\t"orders": ["a"]\n\t\t}\n\t}\n}\n'
        self.assertEqual(qa.line_of(text, ("s", "k", "orders")), 4)
        self.assertIsNone(qa.line_of(text, ("nope",)))


if __name__ == "__main__":
    unittest.main()
