"""``python -m hyprtweaker`` -- the dev loop entrypoint (ADR-0011)."""

from __future__ import annotations

import sys

from hyprtweaker.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
