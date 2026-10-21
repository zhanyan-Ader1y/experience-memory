"""Launch a source plugin without relying on the current working directory."""

from pathlib import Path
import sys


def main(arguments=None):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from experience_memory.__main__ import main as runtime_main

    args = list(sys.argv[1:] if arguments is None else arguments)
    if "--plugin-root" not in args:
        args.extend(["--plugin-root", str(root)])
    return runtime_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
