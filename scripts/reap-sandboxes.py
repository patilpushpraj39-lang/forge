from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "services" / "sandbox-controller" / "src"),
    str(ROOT / "packages" / "repo-intelligence" / "src"),
]

from forge_sandbox_controller.recovery import main


if __name__ == "__main__":
    raise SystemExit(main())
