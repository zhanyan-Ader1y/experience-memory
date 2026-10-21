"""Thin command hook; no storage or business decisions live here."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from launch import main

if __name__ == "__main__":
    raise SystemExit(main(["hook"]))
