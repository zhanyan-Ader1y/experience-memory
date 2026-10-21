import argparse
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

def main(arguments=None):
    parser = argparse.ArgumentParser(description="Run source-only offline tests")
    parser.add_argument("--pattern", default="test*.py")
    args = parser.parse_args(arguments)
    os.environ["EXPERIENCE_MEMORY_LIVE_CONTRACTS"] = "0"
    os.environ["EXPERIENCE_MEMORY_LIVE_FUNCTIONAL"] = "0"
    os.environ.pop("EXPERIENCE_MEMORY_NATIVE_EVIDENCE", None)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    suite = unittest.TestLoader().discover(
        start_dir=str(ROOT / "tests"), pattern=args.pattern, top_level_dir=str(ROOT),
    )
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1

if __name__ == "__main__":
    raise SystemExit(main())
