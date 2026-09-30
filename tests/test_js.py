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
        files = sorted((LAB / "static" / "js").glob("*.js"))
        self.assertGreaterEqual(len(files), 6)
        for f in files:
            with self.subTest(file=f.name):
                p = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)

    def test_page_scripts_exist(self):
        html = (LAB / "static" / "index.html").read_text()
        import re
        for src in re.findall(r'<script src="/static/([^"]+)"', html):
            self.assertTrue((LAB / "static" / src).exists(), src)


if __name__ == "__main__":
    unittest.main()
