from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = [
    ROOT / "packages" / "agent-core" / "src",
    ROOT / "packages" / "repo-intelligence" / "src",
    ROOT / "services" / "api" / "src",
    ROOT / "services" / "sandbox-controller" / "src",
    ROOT / "services" / "worker" / "src",
]

for source_path in SOURCE_PATHS:
    sys.path.insert(0, str(source_path))

suite = unittest.defaultTestLoader.discover(ROOT / "tests", pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
