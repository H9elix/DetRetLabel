"""Windows entry point; normal launches use the unchanged application entry."""
from __future__ import annotations

import sys


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        from smoke_check import run
        raise SystemExit(run(sys.argv[2]))

    from detretlabel.app import main
    raise SystemExit(main())
