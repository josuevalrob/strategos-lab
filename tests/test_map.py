"""L1 acceptance: every option of play.json and of the 12c-2 live log is on the map with its
Petra action, matching REQUIREMENTS.md's table."""

import json
import unittest

from fixture import REAL_REPO, REAL_RUN, real_config

from stlab import mapgraph, server


class MapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = mapgraph.build(real_config(), "spart")
        cls.ids = {n["id"] for n in cls.g["nodes"]}
        cls.nodes = {n["id"]: n for n in cls.g["nodes"]}
        cls.out = {}
        for e in cls.g["edges"]:
            cls.out.setdefault(e["source"], []).append(e["target"])

    def reach(self, start):
        """Every node reachable from start."""
        seen, todo = set(), [start]
        while todo:
            n = todo.pop()
            for t in self.out.get(n, []):
                if t not in seen:
                    seen.add(t)
                    todo.append(t)
        return seen

    def test_columns_and_groups(self):
        groups = {n["group"] for n in self.g["nodes"]}
        self.assertTrue({"input", "part", "question", "option", "action", "manager", "queue"} <= groups)
        self.assertEqual(self.g["code_errors"], [])
        self.assertEqual(self.g["warnings"], [])

    def test_inputs(self):
        for nid in ("in:civ", "in:hero:agis", "in:hero:brasidas", "in:hero:leonidas", "in:hero:pausanias",
                    "in:state"):
            self.assertIn("q:play", self.out.get(nid, []), nid)
        self.assertIn("part:pass_order", self.out["in:opponent"])
        self.assertIn("part:hero_next", self.out["in:opponent"])

    def test_parts_with_trigger_and_file_line(self):
        for pid in ("pass_order", "hero_next", "tower_site", "garrison_now", "wall_now", "petra"):
            n = self.nodes[f"part:{pid}"]
            self.assertTrue(n["details"]["trigger"])
            for a in n["details"]["anchors"]:
                self.assertTrue(a["found"], a)
                self.assertIsInstance(a["line"], int)

    def test_every_play_json_option_reaches_petra(self):
        play = json.loads((REAL_REPO / "source/tools/strategos/questions/play.json").read_text())
        for key in play["questions"]["play"]["criteria"]:
            with self.subTest(option=key):
                if key.endswith(":"):
                    opts = [i for i in self.ids if i.startswith("opt:" + key)]
                else:
                    opts = [f"opt:{key}"]
                self.assertTrue(opts and all(o in self.ids for o in opts), key)
                for o in opts:
                    acts = [t for t in self.out.get(o, []) if t.startswith("act:")]
                    self.assertTrue(acts, f"{o} has no Petra action")

    def test_every_logged_option_is_on_the_map(self):
        path = REAL_REPO / REAL_RUN / "advisor"
        seen = set()
        for f in path.glob("*.jsonl"):
            for line in f.read_text().splitlines()[1:]:
                row = json.loads(line)
                if row.get("kind") == "play":
                    seen |= set(row["options"])
        self.assertIn("garrison:3953", seen)
        for opt in sorted(seen):
            with self.subTest(option=opt):
                nid = mapgraph.option_node_id(opt, self.ids)
                self.assertIsNotNone(nid, opt)
                self.assertTrue([t for t in self.out.get(nid, []) if t.startswith("act:")])

    def test_requirements_table(self):
        """REQUIREMENTS.md: option -> Petra action."""
        expect = {
            "opt:hero:hero_agis": {"mgr:attackManager", "mgr:strategosHero queue"},
            "opt:advance": {"mgr:majorTech queue"},
            "opt:train:workers": {"mgr:villager queue"},
            "opt:train:soldiers": {"mgr:citizenSoldier queue"},
            "opt:hold": {"mgr:defenseManager", "mgr:garrisonManager"},
            "opt:strike": {"mgr:attackManager", "mgr:defenseManager"},
            "opt:fallback": {"mgr:defenseManager"},
            "opt:fortify": {"mgr:defenseManager", "mgr:garrisonManager"},
            "opt:garrison": {"mgr:defenseManager", "mgr:garrisonManager"},
        }
        for opt, managers in expect.items():
            with self.subTest(option=opt):
                self.assertTrue(managers <= self.reach(opt), (opt, self.reach(opt)))
        self.assertEqual(self.out["opt:economy"], ["act:economy"])
        self.assertEqual(self.out.get("act:economy", []), [])
        self.assertIn("act:hold-the-pass/hold", self.out["opt:hold"])
        self.assertIn("act:hero/train", self.out["opt:hero:hero_pausanias"])

    def test_node_details_have_source(self):
        n = self.nodes["q:play"]
        self.assertEqual(n["details"]["edit"], "source/tools/strategos/questions/play.json")
        self.assertIn("economy", n["details"]["criteria"])
        opt = self.nodes["opt:hero:hero_agis"]
        self.assertEqual(opt["details"]["criteria_text"], "train hero agis at the hero building")

    def test_map_path_for_a_logged_question(self):
        entry = {"kind": "play", "trigger": [{"part": "hero_next"}, {"part": "pass_order"}],
                 "options": ["hero:hero_brasidas", "hero:hero_leonidas", "economy", "hold"],
                 "rule": "hero:hero_brasidas", "choice": "hero:hero_leonidas",
                 "game": {"choice": "hero:hero_leonidas",
                          "applied": {"order": "hero/train", "managers": ["attackManager"]}}}
        p = server.map_path(entry, self.ids, self.g["edges"])
        self.assertEqual(p["chosen"], "opt:hero:hero_leonidas")
        self.assertEqual(p["rule"], "opt:hero:hero_brasidas")
        self.assertEqual(p["action"], "act:hero/train")
        self.assertEqual(p["managers"], ["mgr:attackManager"])
        self.assertEqual(p["parts"], ["part:hero_next", "part:pass_order"])
        # An L3 line names the order only: the action comes from the chosen option's edges.
        entry["game"]["applied"] = {"order": "hold", "managers": ["defenseManager"]}
        entry["game"]["choice"] = "hold"
        self.assertEqual(server.map_path(entry, self.ids, self.g["edges"])["action"],
                         "act:hold-the-pass/hold")


if __name__ == "__main__":
    unittest.main()
