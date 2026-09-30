"""L2 acceptance: the timeline of the real 12c-2 Jev live run, the engine.log join, the L3
contract (real L3 run + a synthetic one), and the parsers."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import fixture
from fixture import L3_RUN, REAL_REPO, REAL_RUN

from stlab import config, runs


def raw_line(path: Path, n: int) -> str:
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if i == n:
                return line.rstrip("\n")
    raise AssertionError(f"{path} has no line {n}")


class RealRunTimelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = runs.RunModel("results/phase12c2-jev-live", REAL_REPO / REAL_RUN)
        cls.model.refresh()
        cls.tl = cls.model.timeline("play")
        cls.by_qid = {}
        for e in cls.tl:
            cls.by_qid.setdefault(e["qid"], e)

    def test_114_play_questions(self):
        self.assertEqual(len(self.tl), 114)
        self.assertEqual(self.model.kinds()["play"], 114)
        self.assertEqual(len({e["qid"] for e in self.tl}), 113)   # q#141 was logged twice (superseded)

    def test_game_order(self):
        times = [e["askedTime"] for e in self.tl]
        self.assertEqual(times, sorted(times))
        self.assertEqual(self.tl[0]["qid"], 2)
        self.assertEqual(self.tl[0]["minute"], 3.0)

    def _spot(self, qid, jsonl_line, engine_lines):
        e = self.by_qid[qid]
        adv = next((REAL_REPO / REAL_RUN / "advisor").glob("*.jsonl"))
        raw = json.loads(raw_line(adv, jsonl_line))
        self.assertEqual(e["source"], {"file": adv.name, "line": jsonl_line})
        for key in ("qid", "options", "rule", "choice", "p", "outcome"):
            self.assertEqual(e[key], raw[key], key)
        self.assertEqual(e["events"], raw["features"]["events"])
        self.assertEqual(e["probs"], raw["meta"]["probabilities"])
        eng = REAL_REPO / REAL_RUN / "engine.log"
        for n, needle in engine_lines:
            self.assertIn(needle, raw_line(eng, n))
        return e

    def test_spot_q2_economy(self):
        e = self._spot(2, 2, [(921, "p1 play at pass_order:booming+playbook+phase+rule: asked q#2"),
                              (933, "p1 q#2 play=economy by jev p=0.45"),
                              (934, "q#2 play=economy: no order sent")])
        self.assertEqual(e["game"]["asked"]["line"], 921)
        self.assertEqual(e["game"]["settled"]["line"], 933)
        self.assertEqual(e["game"]["choice"], "economy")
        self.assertEqual(e["game"]["applied"]["state"], "none")
        self.assertEqual(e["game"]["applied"]["line"], 934)
        self.assertEqual([t["part"] for t in e["trigger"]], ["pass_order"])
        self.assertEqual([x["code"] for x in e["trigger"][0]["events"]], ["booming", "playbook", "phase", "rule"])

    def test_spot_q88_tower(self):
        e = self._spot(88, 88, [(4444, "p1 q#88 play=tower:fields by jev p=0.44"),
                                (4445, "q#88/tower_site towers/tower -> defenseManager")])
        a = e["game"]["applied"]
        self.assertEqual((a["state"], a["order"], a["managers"], a["line"]),
                         ("applied", "towers/tower", ["defenseManager"], 4445))
        self.assertEqual([t["part"] for t in e["trigger"]], ["wall_now", "tower_site", "pass_order"])

    def test_spot_q723_hero(self):
        e = self._spot(723, 734, [(14039, "p1 q#723 play=hero:hero_leonidas by jev p=0.3"),
                                  (14040, "q#723/hero_next hero/train -> attackManager")])
        a = e["game"]["applied"]
        self.assertEqual((a["order"], a["managers"]), ("hero/train", ["attackManager"]))
        self.assertEqual(e["rule"], "hero:hero_brasidas")

    def test_rule_fallback_is_marked(self):
        e = self.by_qid[354]
        self.assertIsNone(e["choice"])
        self.assertEqual((e["game"]["choice"], e["game"]["by"]), ("hold", "rule"))
        self.assertEqual(e["game"]["applied"]["state"], "rule")

    def test_no_guessing_without_a_line(self):
        """Pre-L3 runs log no line for a repeated order: those are 'unknown', never guessed."""
        unknown = [e for e in self.tl if e["game"]["applied"]["state"] == "unknown"]
        self.assertGreater(len(unknown), 30)
        for e in unknown:
            self.assertEqual(e["game"]["lines"], [])
            self.assertIn("unknown", e["game"]["applied"]["text"])
        no_line = {e["qid"] for e in self.tl if not e["game"]["lines"] and e["game"]["choice"] != "economy"}
        self.assertEqual(len(no_line), 44)   # the coordinator's count: 44 of 113 play answers

    def test_part_ids_do_not_join(self):
        """'p1 pass_order at …: asked q#N' carries a PART id in 12c-2; it must not join a play row."""
        for e in self.tl:
            for line in e["game"]["lines"]:
                self.assertNotIn("asked q#", line["text"])

    def test_other_kinds(self):
        oc = self.model.timeline("opponent_class")
        self.assertEqual(len(oc), 279)
        self.assertEqual(oc[0]["trigger"][0]["events"][0]["text"], "asked every decision tick")
        self.assertEqual(oc[0]["game"]["applied"]["state"], "applied")
        raid = self.model.timeline("camp_move")
        self.assertEqual(len(raid), 103)
        self.assertTrue(any("camp" in e["game"]["applied"]["text"] for e in raid))

    def test_info(self):
        info = self.model.info()
        self.assertEqual((info["adapter"], info["version"], info["qa_version"]), ("jev", "jev-1.13.0", "f9b1ced7bc"))
        self.assertTrue(info["finished"])
        self.assertFalse(info["live"])


@unittest.skipUnless((REAL_REPO / L3_RUN).exists(), "the real L3 run is not there")
class RealL3RunTest(unittest.TestCase):
    def test_l3_contract(self):
        m = runs.RunModel("results/lab-l3-jev-live", REAL_REPO / L3_RUN)
        m.refresh()
        info = m.info()
        self.assertEqual(info["qa"]["snapshot"], "qa_snapshot")
        self.assertTrue(info["qa_version"].startswith(info["qa"]["commit"][:10]))
        rows = m.rows
        self.assertTrue(all(r.get("io") and r["io"].get("prompt") and r["io"].get("raw_reply") for r in rows))
        tl = m.timeline("play")
        self.assertEqual(len(tl), 5)
        for e in tl:
            self.assertEqual(e["game"]["applied"]["source"], "l3", e["qid"])
        q2 = [e for e in tl if e["qid"] == 2][0]
        self.assertEqual(q2["game"]["applied"]["managers"], ["queueManager"])
        q17 = [e for e in tl if e["qid"] == 17][0]
        self.assertEqual(q17["game"]["applied"]["order"], "hold-the-pass/hold")


class SyntheticL3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = fixture.make_repo()
        cls.dir = fixture.l3_run(cls.repo)
        cls.model = runs.RunModel("results/synthetic-l3", cls.dir)
        cls.model.refresh()
        cls.tl = {e["qid"]: e for e in cls.model.timeline("play")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)

    def test_queue_manager(self):
        a = self.tl[2]["game"]["applied"]
        self.assertEqual((a["state"], a["order"], a["managers"]), ("applied", "train:soldiers", ["queueManager"]))
        self.assertIn("infantry_javelineer_b", a["text"])

    def test_order_with_stratagem_from_routed_line(self):
        a = self.tl[5]["game"]["applied"]
        self.assertEqual((a["order"], a["managers"]), ("hold-the-pass/hold", ["defenseManager", "garrisonManager"]))

    def test_repeat_is_none(self):
        a = self.tl[8]["game"]["applied"]
        self.assertEqual((a["state"], a["order"]), ("none", None))
        self.assertIn("no new order", a["text"])

    def test_rule_without_applied_line(self):
        e = self.tl[11]
        self.assertEqual((e["game"]["choice"], e["game"]["by"], e["game"]["applied"]["state"]),
                         ("economy", "rule", "rule"))
        self.assertIn("rule's pick economy", e["game"]["applied"]["text"])

    def test_io_and_qa(self):
        self.assertTrue(self.tl[2]["has_io"])
        self.assertFalse(self.tl[11]["has_io"])
        self.assertEqual(self.model.info()["qa"]["snapshot"], "qa_snapshot")
        self.assertTrue((self.dir / "advisor" / "qa_snapshot" / "civs" / "spart.json").exists())


class ParseTest(unittest.TestCase):
    def test_engine_index_head_log_format(self):
        idx = runs.EngineIndex(default_player=2)
        idx.feed("p2 play at pass_order:enemy: asked q#7 options hold/economy (rule hold)")
        idx.feed("p2 q#7 play=hold by jev p=0.8 (answered after 5 turns, read 3 later)")
        idx.feed("q#7/pass_order hold-the-pass/hold -> defenseManager, garrisonManager: ok")
        e = idx.get(2, 7)
        self.assertEqual(e["settled"]["choice"], "hold")
        self.assertEqual(runs.applied_summary(e, "hold", "play")["order"], "hold-the-pass/hold")

    def test_players_do_not_mix(self):
        idx = runs.EngineIndex()
        idx.feed(fixture.engine_line(1, "p1 q#3 play=hold by jev p=0.8 (x)"))
        idx.feed(fixture.engine_line(2, "p2 q#3 play=economy by jev p=0.8 (x)"))
        idx.feed(fixture.engine_line(2, "q#3 play=economy: no order sent"))
        self.assertEqual(idx.get(1, 3)["settled"]["choice"], "hold")
        self.assertEqual(idx.get(1, 3)["applied"], [])
        self.assertEqual(len(idx.get(2, 3)["applied"]), 1)

    def test_humanize(self):
        t = runs.humanize_events("play", "hero_next:slot pass_order:dying+phase")
        self.assertEqual([p["part"] for p in t], ["hero_next", "pass_order"])
        self.assertEqual(t[1]["events"][0]["text"], "a new opponent read: dying")
        t = runs.humanize_events("tower_site", "enemy")
        self.assertEqual(t[0]["events"][0]["text"], "an enemy soldier near our front gate")

    def test_registry_discovers_nested_runs(self):
        root = Path(tempfile.mkdtemp(prefix="lab-runs-"))
        try:
            h = fixture.header("abc1234567")
            fixture.write_run(root / "results" / "a" / "b", h, [fixture.play_row(1, ["economy"], "economy", "economy", 3.0, "")], [])
            fixture.write_run(root / "results" / "c", h, [], [])
            cfg = config.Config(repo=root / "norepo", extra_run_roots=[str(root / "results")])
            reg = runs.Registry(cfg)
            self.assertEqual(sorted(reg.scan(force=True)), ["results/a/b", "results/c"])
            self.assertEqual(reg.get("results/a/b").kinds(), {"play": 1})
            self.assertEqual(reg.newest().id in ("results/a/b", "results/c"), True)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
