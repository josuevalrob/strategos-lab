"""Lets `python3 -m unittest tests.test_x` find tests/fixture.py too."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
