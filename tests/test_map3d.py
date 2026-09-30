"""The 3D map's plane / column data (/api/map3d): two flows, fixed positions."""

import unittest

from fixture import real_config

from stlab import map3d, mapgraph


class Map3DTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = map3d.build(real_config(), "spart")
        cls.n = {x["id"]: x for x in cls.g["nodes"]}
        cls.out, cls.inn = {}, {}
        for e in cls.g["edges"]:
            cls.out.setdefault(e["source"], []).append(e["target"])
            cls.inn.setdefault(e["target"], []).append(e["source"])

    def reach(self, start, adj):
        seen, todo = set(), [start]
        while todo:
            for y in adj.get(todo.pop(), []):
                if y not in seen:
                    seen.add(y)
                    todo.append(y)
        return seen

    def test_same_nodes_as_2d_plus_prompt_and_triggers(self):
        flat = {x["id"] for x in mapgraph.build(real_config(), "spart")["nodes"]}
        self.assertTrue(flat <= set(self.n))
        extra = set(self.n) - flat
        self.assertIn("prompt", extra)
        self.assertEqual({i for i in extra if i.startswith("trig:")},
                         {"trig:" + p[5:] for p in flat if p.startswith("part:")})
        self.assertTrue(all("parent" not in x for x in self.g["nodes"]))

    def test_planes(self):
        z = {x["id"]: x["pos"]["z"] for x in self.g["nodes"]}
        self.assertEqual(z["q:play"], 0)
        for x in self.g["nodes"]:
            with self.subTest(node=x["id"]):
                if x["group"] in ("input", "prompt"):
                    self.assertEqual(x["plane"], "reads")
                    self.assertGreater(x["pos"]["z"], 0)
                elif x["id"] != "q:play":
                    self.assertEqual(x["plane"], "pick")
                    self.assertLess(x["pos"]["z"], 0)
        self.assertEqual([p["id"] for p in self.g["planes"]], ["reads", "pick"])
        self.assertEqual({c["plane"] for c in self.g["columns"]}, {"reads", "pick"})

    def test_columns_left_to_right(self):
        col = {"input": 0, "trigger": 0, "prompt": 1, "part": 1, "option": 2, "question": 2,
               "action": 3, "manager": 4, "queue": 4}
        for x in self.g["nodes"]:
            self.assertEqual(x["col"], col[x["group"]], x["id"])
            self.assertEqual(x["pos"]["x"], (x["col"] - 2) * map3d.COL_X)

    def test_fixed_distinct_positions(self):
        pos = [(x["pos"]["x"], x["pos"]["y"], x["pos"]["z"]) for x in self.g["nodes"]]
        self.assertEqual(len(pos), len(set(pos)))
        self.assertEqual(map3d.build(real_config(), "spart")["nodes"][5]["pos"], self.g["nodes"][5]["pos"])

    def test_reads_flow_meets_the_pick_flow_at_play(self):
        """Inputs no longer point at the options: they feed the prompt, which feeds play."""
        for nid in ("in:civ", "in:hero:pausanias", "in:state"):
            self.assertIn("prompt", self.out[nid])
            self.assertNotIn("q:play", self.out[nid])
        self.assertEqual(self.out["prompt"], ["q:play"])
        opts = [x["id"] for x in self.g["nodes"] if x["group"] == "option"]
        self.assertEqual(sorted(self.out["q:play"]), sorted(opts))
        self.assertEqual(self.out["trig:hero_next"], ["part:hero_next"])

    def test_edge_kinds_and_dashes(self):
        kinds = {e["id"]: e for e in self.g["edges"]}
        e = kinds["in:hero:pausanias->opt:hero:hero_pausanias"]
        self.assertEqual((e["kind"], e["dashed"]), ("describes", True))
        e = kinds["in:civ->prompt"]
        self.assertEqual((e["kind"], e["dashed"]), ("reads", False))
        self.assertEqual(kinds["in:civ->part:hero_next"]["kind"], "config")
        self.assertEqual(kinds["opt:hold->act:hold-the-pass/hold"]["kind"], "petra")
        self.assertEqual({l["kind"] for l in self.g["legend"]}, {e["kind"] for e in self.g["edges"]})

    def test_focus_paths(self):
        """What the page lights up on a click: the node's whole upstream and downstream."""
        up = self.reach("opt:hero:hero_pausanias", self.inn)
        down = self.reach("opt:hero:hero_pausanias", self.out)
        self.assertTrue({"part:hero_next", "trig:hero_next", "q:play", "prompt", "in:hero:pausanias",
                         "in:civ"} <= up)
        self.assertEqual(down, {"act:hero/train", "mgr:attackManager", "mgr:strategosHero queue"})
        down = self.reach("q:play", self.out)
        self.assertIn("mgr:citizenSoldier queue", down)
        self.assertNotIn("part:hero_next", down)

    def test_details_for_every_node(self):
        for x in self.g["nodes"]:
            self.assertTrue(x["details"].get("title"), x["id"])


if __name__ == "__main__":
    unittest.main()
