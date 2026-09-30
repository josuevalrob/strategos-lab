"""HTTP smoke: start lab.py as a process on a spare port against a FIXTURE repo, hit the page,
the static files and every API endpoint (GET and POST), then stop it."""

import json
import shutil
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

import fixture

from stlab import config, server

PLAY = f"{config.QUESTIONS_DIR}/play.json"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HttpSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = fixture.make_repo()
        cls.run_dir = fixture.l3_run(cls.repo, "smoke-l3")
        old = fixture.write_run(cls.repo / ".claude/strategos/results/smoke-old",
                                fixture.header("0" * 40, mod_sha="deadbeef00"),
                                [fixture.play_row(3, ["hold", "economy"], "hold", "hold", 4.0, "pass_order:enemy")],
                                [fixture.engine_line(1, "p1 q#3 play=hold by jev p=0.5 (x)")])
        (old / "summary.json").write_text("{}")
        cls.port = free_port()
        cls.cache = cls.repo.parent / (cls.repo.name + "-cache")
        cls.proc = subprocess.Popen([sys.executable, str(fixture.LAB / "lab.py"), "--repo", str(cls.repo),
                                     "--port", str(cls.port), "--cache", str(cls.cache)],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        cls.banner = cls.proc.stdout.readline()
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.proc.stdout.close()
        shutil.rmtree(cls.repo, ignore_errors=True)
        shutil.rmtree(cls.cache, ignore_errors=True)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path, status=200, raw=False):
        try:
            with urllib.request.urlopen(self.url(path), timeout=60) as r:
                self.assertEqual(r.status, status, path)
                body = r.read()
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            with e:
                text = e.read()
            self.assertEqual(e.code, status, f"{path}: {text[:300]}")
            return json.loads(text or b"{}") if not raw else b""

    def post(self, path, body, status=200, headers=None):
        h = {"Content-Type": "application/json", "X-Lab": "1"}
        h.update(headers or {})
        req = urllib.request.Request(self.url(path), data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                self.assertEqual(r.status, status, path)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                text = e.read()
            self.assertEqual(e.code, status, f"{path}: {text[:300]}")
            return json.loads(text or b"{}")

    def test_0_banner(self):
        self.assertIn(f"http://127.0.0.1:{self.port}/", self.banner)
        self.assertIn("strategos/mvp1", self.banner)

    def test_page_and_static(self):
        html = self.get("/", raw=True).decode()
        self.assertIn("<title>Strategos Lab</title>", html)
        for src in ("/static/style.css", "/static/vendor/cytoscape.min.js", "/static/js/app.js",
                    "/static/js/graph.js", "/static/js/mapview.js", "/static/js/game.js",
                    "/static/js/models.js", "/static/js/editor.js"):
            self.assertIn(src, html)
            self.assertTrue(len(self.get(src, raw=True)) > 100, src)
        for src in ("/static/js/map3d.js", "/static/js/map3d-boot.mjs", "/static/vendor/3d-force-graph.min.js",
                    "/static/vendor/three/three.module.js", "/static/vendor/three/three.core.js",
                    "/static/vendor/three-spritetext.mjs"):
            self.assertTrue(len(self.get(src, raw=True)) > 300, src)
        self.assertIn('"three": "/static/vendor/three/three.module.js"', html)
        self.get("/static/../lab.py", status=404, raw=True)
        self.get("/nope", status=404)

    def test_get_endpoints(self):
        info = self.get("/api/info")
        self.assertEqual(info["branch"], "strategos/mvp1")
        self.assertIn("results/smoke-l3", info["live_runs"])       # fresh, no summary.json
        self.assertNotIn("results/smoke-old", info["live_runs"])
        self.assertIn("NEXT game only", info["live_note"])          # its header has a qa pin
        self.assertEqual(info["code_errors"], [])
        files = self.get("/api/files")
        self.assertEqual([c["civ"] for c in files["civs"]], ["spart"])
        f = self.get("/api/file?path=" + PLAY)
        self.assertEqual(f["validation"]["errors"], [])
        self.assertIn("play", f["choices"]["known_options"])
        self.get("/api/file?path=unrelated.txt", status=400)
        dj = self.get("/api/file?path=" + config.DOCTRINE_JSON)
        self.assertEqual(dj["kind"], "doctrine")
        self.assertEqual(dj["validation"]["errors"], [])
        self.assertEqual(dj["choices"]["stratagem_orders"]["hold-the-pass"], ["hold", "strike", "fallback"])
        self.assertIn("qa_snapshot", dj["choices"]["note"])
        bad = json.loads(dj["text"])
        bad["stratagems"]["hold-the-pass"]["orders"].append("trade")
        self.assertTrue(self.post("/api/validate", {"path": config.DOCTRINE_JSON, "data": bad})["errors"])
        self.assertEqual(self.post("/api/format", {"path": config.DOCTRINE_JSON, "data": json.loads(dj["text"])})["text"],
                         dj["text"])
        m = self.get("/api/map?civ=spart")
        self.assertGreater(len(m["nodes"]), 40)
        m3 = self.get("/api/map3d?civ=spart")
        self.assertEqual([p["id"] for p in m3["planes"]], ["reads", "pick"])
        self.assertTrue(all("pos" in n and "plane" in n for n in m3["nodes"]))
        line = [n for n in m["nodes"] if n["id"] == "part:hero_next"][0]["details"]["anchors"][0]["line"]
        src = self.get(f"/api/source?file=head.js&line={line}")
        self.assertTrue(any(x["n"] == line for x in src["lines"]))
        self.get("/api/source?file=../../x&line=1", status=400)
        rs = self.get("/api/runs")
        ids = [r["id"] for r in rs["runs"]]
        self.assertIn("results/smoke-l3", ids)
        tl = self.get("/api/timeline?run=results/smoke-l3&kind=play")
        self.assertEqual(len(tl["entries"]), 5)
        self.assertEqual(self.get(f"/api/timeline?run=results/smoke-l3&kind=play&since={tl['rev']}")["entries"], [])
        q = self.get("/api/question?run=results/smoke-l3&idx=0")
        self.assertEqual(q["io"]["mode"], "exact")
        self.assertEqual(q["map_path"]["chosen"], "opt:train:soldiers")
        q = self.get("/api/question?run=results/smoke-old&idx=0")
        self.assertEqual(q["io"]["mode"], "rebuilt from current files")
        self.get("/api/question?run=results/nope&idx=0", status=404)
        live = self.get("/api/live?run=newest&kind=play")
        self.assertIn(live["run"]["id"], ids)
        models = self.get("/api/models")
        self.assertTrue(models["groups"])
        h = self.get("/api/history?path=" + PLAY)
        self.assertEqual(h["log"][0]["subject"], "fixture")
        d = self.get(f"/api/diff?path={PLAY}&a=HEAD&b=working")
        self.assertTrue(d["same"])
        s = self.get(f"/api/show?path={PLAY}&sha=HEAD")
        self.assertIn('"stratagem": "play"', s["text"])
        cc = self.get("/api/civcodes")
        self.assertIn("rome", [c["code"] for c in cc["codes"]])

    def test_post_endpoints(self):
        doc = self.get("/api/file?path=" + PLAY)
        data = doc["data"]
        data["questions"]["play"]["criteria"]["advance"] = "go to the next phase now"
        v = self.post("/api/validate", {"path": PLAY, "data": data})
        self.assertEqual(v["errors"], [])
        bad = json.loads(json.dumps(data))
        bad["questions"]["play"]["criteria"]["charge"] = "x"
        self.assertTrue(self.post("/api/validate", {"path": PLAY, "data": bad})["errors"])
        f = self.post("/api/format", {"path": PLAY, "data": json.loads(doc["text"])})
        self.assertEqual(f["text"], doc["text"])                  # unchanged data -> the file's own bytes
        self.post("/api/format", {"path": "unrelated.txt", "data": {}}, status=400)
        d = self.post("/api/diff", {"path": PLAY, "a": "working", "b": "draft", "draft_data": data})
        self.assertIn("+    \"advance\": \"go to the next phase now\",", d["diff"])
        r = self.post("/api/apply", {"changes": [{"path": PLAY, "data": bad}], "summary": "x"}, status=422)
        self.assertEqual(r["code"], "validation")
        r = self.post("/api/apply", {"changes": [{"path": PLAY, "data": data, "base_sha256": doc["sha256"]}],
                                     "summary": "advance wording"})
        self.assertEqual(r["files"], [PLAY])
        self.assertEqual(fixture.git(self.repo, "log", "-1", "--format=%s").strip(),
                         "Strategos lab: advance wording")
        first = fixture.git(self.repo, "rev-list", "--max-parents=0", "HEAD").strip()
        r = self.post("/api/restore", {"path": PLAY, "sha": first})
        self.assertEqual(r["files"], [PLAY])
        pv = self.post("/api/clone/preview", {"src": "spart", "dst": "gaul", "name": "Gauls"})
        self.assertTrue(pv["ok"])
        self.assertTrue(any("gaul" in i["what"] for i in pv["issues"]))
        r = self.post("/api/clone", {"src": "spart", "dst": "gaul", "name": "Gauls"})
        self.assertEqual(len(r["files"]), 5)
        r = self.post("/api/clone", {"src": "spart", "dst": "gaul"}, status=400)
        self.assertEqual(r["code"], "clone")

    def test_post_guards(self):
        self.post("/api/apply", {"changes": []}, status=403, headers={"X-Lab": "0"})
        req = urllib.request.Request(self.url("/api/apply"), data=b"{}", method="POST",
                                     headers={"Content-Type": "text/plain", "X-Lab": "1"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 403)
        cm.exception.close()
        req = urllib.request.Request(self.url("/api/info"), headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 403)
        cm.exception.close()
        self.post("/api/nope", {}, status=404)
        self.post("/api/validate", {"path": "../../etc/passwd", "text": "{}"}, status=400)
        self.post("/api/diff", {"path": "../../etc/passwd", "draft_data": {}}, status=400)

    def test_every_route_was_hit(self):
        """Keep this file honest: a new route needs a line in this smoke test."""
        from pathlib import Path
        src = Path(__file__).read_text()
        for route in list(server.GET_ROUTES) + list(server.POST_ROUTES):
            self.assertIn(route, src, route)

    def test_zz_binds_localhost_only(self):
        self.assertIsNone(self.proc.poll())
        # Only 127.0.0.1 is bound: the server never listens on another interface.
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout:
            self.assertIn("127.0.0.1:", out.stdout)
            self.assertNotIn("*:", out.stdout)


if __name__ == "__main__":
    unittest.main()
