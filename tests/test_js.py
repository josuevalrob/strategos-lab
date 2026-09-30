"""`node --check` on every JS file the page loads (skipped when node is missing)."""

import shutil
import subprocess
import unittest
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)


@unittest.skipUnless(NODE, "node is not installed")
class NodeCheckTest(unittest.TestCase):
    def test_node_check(self):
        files = sorted((LAB / "static" / "js").glob("*.js")) + sorted((LAB / "static" / "js").glob("*.mjs"))
        self.assertGreaterEqual(len(files), 8)
        for f in files:
            with self.subTest(file=f.name):
                p = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)

    def test_import_map_targets_exist(self):
        import json
        import re
        html = (LAB / "static" / "index.html").read_text()
        m = re.search(r'<script type="importmap">(.*?)</script>', html, re.S)
        for target in json.loads(m.group(1))["imports"].values():
            self.assertTrue((LAB / target.lstrip("/")).exists(), target)
        boot = (LAB / "static" / "js" / "map3d-boot.mjs").read_text()
        self.assertIn('"/static/vendor/3d-force-graph.min.js"', boot)
        self.assertTrue((LAB / "static" / "vendor" / "3d-force-graph.min.js").exists())

    def test_page_scripts_exist(self):
        html = (LAB / "static" / "index.html").read_text()
        import re
        for src in re.findall(r'<script src="/static/([^"]+)"', html):
            self.assertTrue((LAB / "static" / src).exists(), src)


if __name__ == "__main__":
    unittest.main()
