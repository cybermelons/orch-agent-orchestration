#!/usr/bin/env python3
"""Root-level shim for the spawn CLI.

The agent docs hand every level the literal command `~/orch/spawn.py <role>
<scope...>`, so the path agents paste must exist at the repo root. The
implementation lives in `orch/spawn.py`; this only puts the repo root on
sys.path so `from orch import core` resolves when invoked as a script.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch.spawn import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
