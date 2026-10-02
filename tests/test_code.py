"""The head.js parser and the hand-kept anchor table (L1)."""

import unittest

from fixture import REAL_REPO, real_config  # noqa: F401  (sets sys.path)

from stlab import code, config


class ParserTest(unittest.TestCase):
    SRC = '''
const ORDER_ROUTES = {
	// a comment with "quotes": ["not", "a", "route"]
	"strike": ["attackManager", "defenseManager"],
	"hold": ["defenseManager", "garrisonManager"], // trailing
	"export": []
};

const MANAGERLESS_ORDERS = new Set(["export"]);
const ECONOMY = "economy";
const TRAIN_WORKERS = "train:workers";
'''

    def test_object_of_lists(self):
        routes, lines = code.parse_object_of_lists(self.SRC, "ORDER_ROUTES")
        self.assertEqual(routes, {"strike": ["attackManager", "defenseManager"],
                                  "hold": ["defenseManager", "garrisonManager"], "export": []})
        self.assertEqual(lines["strike"], 4)
        self.assertNotIn("not", routes)

    def test_set_and_consts(self):
        self.assertEqual(code.parse_string_set(self.SRC, "MANAGERLESS_ORDERS"), ["export"])
        self.assertEqual(code.parse_string_consts(self.SRC)["TRAIN_WORKERS"], "train:workers")

    def test_missing_literal_raises(self):
        with self.assertRaises(ValueError):
            code.parse_object_of_lists("const X = 1;", "ORDER_ROUTES")

    def test_find_anchor(self):
        line, count = code.find_anchor("a\nb\n\tfoo(x)\nc", "\tfoo(x)\n")
        self.assertEqual((line, count), (3, 1))
        self.assertEqual(code.find_anchor("abc", "zzz"), (None, 0))

    def test_play_token(self):
        self.assertEqual(code.play_token("hero_next", "hero_agis"), "hero:hero_agis")
        self.assertEqual(code.play_token("hero_next", "wait"), "economy")
        self.assertEqual(code.play_token("tower_site", "gate"), "tower:gate")
        self.assertEqual(code.play_token("garrison_now", "garrison", "3953"), "garrison:3953")
        self.assertEqual(code.play_token("garrison_now", "keep_working"), "economy")
        self.assertEqual(code.play_token("pass_order", "hold"), "hold")


class RealHeadJsTest(unittest.TestCase):
    """Against the real head.js (read only).  Fails loudly when head.js moves on."""

    @classmethod
    def setUpClass(cls):
        cls.facts = code.load(real_config())

    def test_no_parse_errors(self):
        self.assertEqual(self.facts.errors, [])

    def test_order_routes(self):
        r = self.facts.order_routes
        self.assertEqual(r["hold"], ["defenseManager", "garrisonManager"])
        self.assertEqual(r["strike"], ["attackManager", "defenseManager"])
        self.assertEqual(r["fallback"], ["defenseManager"])
        self.assertEqual(r["train"], ["attackManager"])
        self.assertEqual(r["tower"], ["defenseManager"])
        self.assertEqual(r["wall"], ["basesManager"])
        self.assertIn("export", self.facts.managerless)

    def test_stratagem_orders(self):
        s = self.facts.stratagem_orders
        self.assertEqual(s["hold-the-pass"], ["hold", "strike", "fallback"])
        self.assertEqual(s["fortify"], ["fortify", "garrison", "standdown"])
        self.assertEqual(s["hero"], ["prepare", "train"])
        self.assertEqual(s["guard"], ["garrison"])

    def test_consts(self):
        c = self.facts.consts
        self.assertEqual((c["ECONOMY"], c["WAIT"], c["ADVANCE"], c["TRAIN_WORKERS"], c["TRAIN_SOLDIERS"]),
                         ("economy", "wait", "advance", "train:workers", "train:soldiers"))
        self.assertEqual(self.facts.tower_sites, ["choke", "fields", "gate"])
        self.assertIn("massing", self.facts.classes)
        self.assertIn("game_minute", self.facts.play_state_keys)

    def test_every_anchor_exists_exactly_once(self):
        files = code.read_code(real_config())
        for aid, (fkey, needle) in code.ANCHORS.items():
            with self.subTest(anchor=aid):
                self.assertEqual(files[fkey].count(needle), 1,
                                 f"anchor {aid!r} ({fkey}) must occur exactly once: {needle.strip()!r}")
                self.assertTrue(self.facts.anchors[aid]["found"])

    def test_parts_and_actions_only_use_known_anchors(self):
        used = [a for p in code.PARTS for a in p["anchors"]]
        used += [a for d in code.DIRECT_ACTIONS.values() for a in d["anchors"]]
        for a in used:
            self.assertIn(a, code.ANCHORS, a)

    def test_direct_actions_dispatch(self):
        """Every hand-kept option -> (stratagem, order) is one head.js dispatch accepts."""
        for key, d in code.DIRECT_ACTIONS.items():
            if d["stratagem"] is None:
                continue
            with self.subTest(option=key):
                self.assertIn(d["order"], self.facts.stratagem_orders[d["stratagem"]])
                self.assertTrue(self.facts.order_routes.get(d["order"]))

    def test_anchor_lines_are_where_the_brief_says(self):
        """Sanity against the brief's line numbers (+-40: head.js keeps moving)."""
        near = {"order_routes": 51, "stratagem_orders": 115, "dispatch": 811, "route": 900,
                "play_token": 1989, "ask_play": 2158, "apply_play_answer": 2194}
        for aid, line in near.items():
            self.assertLess(abs(self.facts.anchors[aid]["line"] - line), 40, aid)


if __name__ == "__main__":
    unittest.main()
